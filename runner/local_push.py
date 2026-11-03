"""Exact GitHub push command for a verified bare snapshot and native gh login."""
from pathlib import Path
import re
import shlex
from runner.git_push import push_argv
from runner.trace import trace

class LocalPushError(RuntimeError): pass

def local_push_command(bare,gh,repo,branch,head,expected_remote):
    try:
        if not isinstance(bare,str) or not Path(bare).is_absolute() or not isinstance(gh,str) or not Path(gh).is_absolute() or any(c in bare+gh for c in '\r\n\0') or not isinstance(repo,str) or any(part in ('.','..') for part in repo.split('/')): raise ValueError()
        args=push_argv(repo,branch,head,expected_remote)
        args[0]='/usr/bin/git';args.insert(1,'--git-dir='+bare)
        index=args.index('push')
        args[index:index]=['-c','credential.helper=!'+shlex.quote(gh)+' auth git-credential']
        return args
    except Exception:raise LocalPushError('invalid exact push target')

# One attempt row per approved action and head: reserved -> done | failed | not_executed.
# 'reserved' means the launch outcome is unknown; the observer treats it as not quiescent.
def ensure_pr_attempts(db):
    db.execute('CREATE TABLE IF NOT EXISTS local_pr_attempts(run TEXT NOT NULL,action TEXT NOT NULL,version TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(run,action,version))')

def reserve_pr_attempt(db,run,action,version,kind):
    # Only an attempt that did not succeed may be reserved again, under a fresh approval.
    return db.execute("INSERT INTO local_pr_attempts VALUES(?,?,?,?,'reserved') ON CONFLICT(run,action,version) DO UPDATE SET state='reserved',kind=excluded.kind WHERE local_pr_attempts.state IN ('failed','not_executed')",(run,action,version,kind)).rowcount==1

def finish_pr_attempt(db,run,action,version,state):
    if state not in ('done','failed','not_executed'): raise ValueError('attempt state')
    return db.execute("UPDATE local_pr_attempts SET state=? WHERE run=? AND action=? AND version=? AND state='reserved'",(state,run,action,version)).rowcount==1

class LocalPushTransport:
    """Push one exact approved commit from a verified bare snapshot."""
    def __init__(self,service,run,queue_claim,graph_claim,gh):
        self.service,self.run,self.queue_claim,self.graph_claim,self.gh=service,run,queue_claim,graph_claim,gh
        ensure_pr_attempts(service.graph.db)
    def push_exact(self,branch,head):
        import hashlib,json,secrets,time
        from runner.engineering_dispatch import workspace_snapshot
        from runner.local_git_snapshot import scoped_git_snapshot
        from runner.local_command import _run_scoped
        from runner.git_push import require_fast_forward
        service=self.service;s=service.model_supervisor;db=service.graph.db
        if s is None or s.preflight() is not True or s.fence(self.run) is not True or not service.queue.may_start(self.run,self.queue_claim) or self.gh._digest()!=self.gh.digest: raise LocalPushError('local push boundary unavailable')
        service.graph._lease(self.run,self.graph_claim)
        workspace=workspace_snapshot(service,self.run)
        if workspace['branch']!=branch or workspace['head']!=head: raise LocalPushError('push target changed')
        repo=workspace['repository'];scope='github:'+repo+':branch:'+branch;assignment='job-'+secrets.token_hex(24)
        db.execute('BEGIN IMMEDIATE')
        try:
            service.graph._lease(self.run,self.graph_claim)
            if not service.queue.may_start(self.run,self.queue_claim) or s.fence(self.run) is not True: raise LocalPushError('task claim unavailable')
            rows=db.execute("SELECT a.action,b.manifest FROM external_actions a JOIN action_scopes z ON z.run=a.run AND z.action=a.action AND z.version=a.version JOIN action_bindings b ON b.run=a.run AND b.action=a.action AND b.version=a.version WHERE a.run=? AND a.version=? AND a.status='uncertain' AND a.action LIKE 'push:%' AND z.scope=?",(self.run,head,scope)).fetchall()
            if len(rows)!=1: raise LocalPushError('exact push approval unavailable')
            action,manifest=rows[0];target=json.loads(manifest)
            proposals=[json.loads(service.artifacts.get(ref)) for (ref,) in db.execute("SELECT proposal FROM pr_bridge_operations WHERE run=? AND kind='push' AND state IN ('approved','uncertain')",(self.run,)).fetchall()]
            selected=[p for p in proposals if p.get('action')==action and p.get('target')==target]
            if len(selected)!=1 or set(selected[0].get('args',{}))!={'expected_remote'}: raise LocalPushError('exact push proposal unavailable')
            expected=selected[0]['args']['expected_remote']
            local_push_command('/placeholder/repo.git',str(self.gh.executable),repo,branch,head,expected)
            if target.get('repo')!=repo or target.get('branch')!=branch or target.get('head')!=head or target.get('diff')!=hashlib.sha256(workspace['diff'].encode()).hexdigest(): raise LocalPushError('approved push target changed')
            if not reserve_pr_attempt(db,self.run,action,head,'push'): raise LocalPushError('push attempt already recorded')
            db.execute('COMMIT')
        except Exception:db.execute('ROLLBACK');raise
        launched=False; pushed=False
        def before_push(unit):
            nonlocal launched
            service.graph._lease(self.run,self.graph_claim)
            live=db.execute("SELECT 1 FROM external_actions a JOIN action_scopes z ON z.run=a.run AND z.action=a.action AND z.version=a.version WHERE a.run=? AND a.action=? AND a.version=? AND a.status='uncertain' AND z.scope=?",(self.run,action,head,scope)).fetchone()
            if not live or self.gh._digest()!=self.gh.digest or workspace_snapshot(service,self.run,assignment=assignment)!=workspace or s.fence(self.run) is not True or not service.queue.may_start(self.run,self.queue_claim): raise LocalPushError('push authorization changed')
            launched=True
        try:
            with scoped_git_snapshot(s,self.run,workspace['path'],head,lambda _:None,workspace_claim=(s.engineering_root,assignment,s.policy_hash)) as (bare,bundle_unit):
                require_fast_forward(bare,expected,head)
                if workspace_snapshot(service,self.run,assignment=assignment)!=workspace or s.fence(self.run) is not True or not service.queue.may_start(self.run,self.queue_claim): raise LocalPushError('workspace or task changed')
                env=dict(self.gh.environment);env.update({'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','GIT_NO_REPLACE_OBJECTS':'1'})
                command=local_push_command(bare,str(self.gh.executable),repo,branch,head,expected)
                result=_run_scoped(s,self.run,bare,command,env,time.monotonic()+60,8192,lambda _:None,before_push)
                if result['exit_code']!=0: raise LocalPushError('push outcome uncertain')
                pushed=True
                if not finish_pr_attempt(db,self.run,action,head,'done'): raise LocalPushError('push attempt changed')
                self._release_bundle(assignment,bundle_unit)
            if workspace_snapshot(service,self.run)!=workspace: raise LocalPushError('workspace changed after push')
            return True
        except Exception as failure:
            self._settle_failure(action,head,assignment,pushed,launched)
            raise LocalPushError('push outcome requires inspection') from failure
    def _release_bundle(self,assignment,bundle_unit):
        from runner.engineering_workspace import EngineeringWorkspace
        registry=EngineeringWorkspace(self.service.model_supervisor.engineering_root)
        try: registry.release_local(self.run,assignment,bundle_unit)
        finally: registry.close()
    def _settle_failure(self,action,head,assignment,pushed,launched):
        # A success is already recorded as done; after a launch the observer reconciles against the remote.
        if pushed: return
        from runner.local_scope import unresolved_scope_rows
        from runner.engineering_workspace import EngineeringWorkspace
        db=self.service.graph.db
        try:
            if unresolved_scope_rows(db,self.run): return  # a scope may still run: stay reserved, so the observer does not treat it as quiescent
            finish_pr_attempt(db,self.run,action,head,'failed' if launched else 'not_executed')
            if not launched:
                # Pre-launch failure: give back the workspace claim so the next attempt can take it.
                registry=EngineeringWorkspace(self.service.model_supervisor.engineering_root)
                try:
                    if registry.db.execute('SELECT assignment FROM engineering_workspaces WHERE run=?',(self.run,)).fetchone()==(assignment,): registry.release(self.run,assignment)
                finally: registry.close()
        except Exception as exc:
            trace('push.settle_failed', run=self.run, action=action[:24], error=f'{type(exc).__name__}: {str(exc)[:200]}')
