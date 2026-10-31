"""Exact GitHub push command for a verified bare snapshot and native gh login."""
from pathlib import Path
import re
import shlex
from runner.docker_push import push_argv

class LocalPushError(RuntimeError): pass

def local_push_command(bare,gh,repo,branch,head,expected_remote):
    try:
        if not isinstance(bare,str) or not Path(bare).is_absolute() or not isinstance(gh,str) or not Path(gh).is_absolute() or any(c in bare+gh for c in '\r\n\0') or not isinstance(repo,str) or any(part in ('.','..') for part in repo.split('/')): raise ValueError()
        args=push_argv(repo,branch,head,expected_remote)
        args[0]='/usr/bin/git';args.insert(1,'--git-dir='+bare)
        index=args.index('push')
        args[index:index]=['-c','credential.helper=!'+shlex.quote(gh)+' auth git-credential']
        return args
    except Exception:raise LocalPushError('invalid exact push target') from None

class LocalPushTransport:
    """Push one exact approved commit from a verified bare snapshot."""
    def __init__(self,service,run,queue_claim,graph_claim,gh):
        self.service,self.run,self.queue_claim,self.graph_claim,self.gh=service,run,queue_claim,graph_claim,gh
        db=service.graph.db
        db.execute('CREATE TABLE IF NOT EXISTS local_push_attempts(assignment TEXT PRIMARY KEY,run TEXT NOT NULL,action TEXT NOT NULL,scope TEXT NOT NULL,head TEXT NOT NULL,state TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS local_pr_scopes(unit TEXT PRIMARY KEY,assignment TEXT NOT NULL,run TEXT NOT NULL,action TEXT NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL)')
    def push_exact(self,branch,head):
        import hashlib,json,secrets,time
        from runner.engineering_dispatch import workspace_snapshot
        from runner.engineering_workspace import EngineeringWorkspace
        from runner.local_git_snapshot import scoped_git_snapshot
        from runner.local_command import _run_scoped
        from runner.docker_push import require_fast_forward
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
            db.execute("INSERT INTO local_push_attempts VALUES(?,?,?,?,?,'reserved')",(assignment,self.run,action,scope,head));db.execute('COMMIT')
        except Exception:db.execute('ROLLBACK');raise
        registry=EngineeringWorkspace(s.engineering_root)
        try:
            if registry.claim(self.run,assignment,s.policy_hash)!=workspace['path']:raise LocalPushError('workspace changed')
        finally:registry.close()
        def record(kind):
            def save(unit): db.execute('INSERT INTO local_pr_scopes VALUES(?,?,?,?,?,?)',(unit,assignment,self.run,action,scope,kind))
            return save
        def bind(unit):
            registry=EngineeringWorkspace(s.engineering_root)
            try:registry.bind_local_scope(self.run,assignment,unit)
            finally:registry.close()
        try:
            with scoped_git_snapshot(s,self.run,workspace['path'],head,record('bundle'),on_started=bind) as (bare,bundle_unit):
                require_fast_forward(bare,expected,head)
                if workspace_snapshot(service,self.run,assignment=assignment)!=workspace or s.fence(self.run) is not True or not service.queue.may_start(self.run,self.queue_claim): raise LocalPushError('workspace or task changed')
                env=dict(self.gh.environment);env.update({'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','GIT_NO_REPLACE_OBJECTS':'1'})
                command=local_push_command(bare,str(self.gh.executable),repo,branch,head,expected)
                def before_push(unit):
                    service.graph._lease(self.run,self.graph_claim)
                    live=db.execute("SELECT 1 FROM external_actions a JOIN action_scopes z ON z.run=a.run AND z.action=a.action AND z.version=a.version WHERE a.run=? AND a.action=? AND a.version=? AND a.status='uncertain' AND z.scope=?",(self.run,action,head,scope)).fetchone()
                    if not live or self.gh._digest()!=self.gh.digest or workspace_snapshot(service,self.run,assignment=assignment)!=workspace or s.fence(self.run) is not True or not service.queue.may_start(self.run,self.queue_claim): raise LocalPushError('push authorization changed')
                result=_run_scoped(s,self.run,bare,command,env,time.monotonic()+60,8192,record('push'),before_push)
                if result['exit_code']!=0:raise LocalPushError('push outcome uncertain')
                from runner.local_scope import valid_scope_identity
                row=db.execute("SELECT x.invocation,x.cgroup FROM local_pr_scopes p JOIN local_scopes x ON x.unit=p.unit AND x.run=p.run WHERE p.assignment=? AND p.kind='push' AND p.unit=? AND x.state='terminated'",(assignment,result['unit'])).fetchone()
                if not row or not valid_scope_identity(result['unit'],*row):raise LocalPushError('push termination unverified')
                if db.execute("UPDATE local_push_attempts SET state='terminated' WHERE assignment=? AND state='reserved'",(assignment,)).rowcount!=1:raise LocalPushError('push assignment changed')
                registry=EngineeringWorkspace(s.engineering_root)
                try:registry.release_local(self.run,assignment,bundle_unit)
                finally:registry.close()
            if workspace_snapshot(service,self.run)!=workspace:raise LocalPushError('workspace changed after push')
            return True
        except Exception:raise LocalPushError('push outcome requires inspection') from None
