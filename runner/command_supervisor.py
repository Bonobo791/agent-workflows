"""Owner-approved test commands with durable, supervisor-observed outcomes.

The backend is a trusted process-boundary interface; none is enabled by default.
Timeouts and ambiguous backend results retain the workspace fence.
"""
import hashlib
import json
import re
import secrets
import sqlite3
import time

class CommandError(RuntimeError): pass

class CommandSupervisor:
    def __init__(self,service,spec=None,approval=None,backend=None):
        self.service=service; self.backend=backend; self.spec=spec
        self.policy_hash=None
        if spec is not None:
            if not isinstance(spec,dict) or set(spec)!={'argv','timeout_seconds','output_limit'} or not isinstance(spec['argv'],list) or not spec['argv'] or len(spec['argv'])>32 or any(not isinstance(arg,str) or not arg or '\x00' in arg or len(arg)>4096 for arg in spec['argv']) or type(spec['timeout_seconds']) is not int or not 1<=spec['timeout_seconds']<=600 or type(spec['output_limit']) is not int or not 1<=spec['output_limit']<=1048576: raise CommandError('invalid exact command policy')
            self.spec=json.loads(json.dumps(spec)); self.policy_hash=hashlib.sha256(json.dumps(self.spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if approval!=self.policy_hash: raise CommandError('exact command policy approval required')
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS supervised_commands(id TEXT PRIMARY KEY,run TEXT NOT NULL,commit_id TEXT NOT NULL,workspace_hash TEXT NOT NULL,policy_hash TEXT NOT NULL,state TEXT NOT NULL,record_ref TEXT,started REAL NOT NULL)')
    def ready(self):
        if self.spec is None or not callable(getattr(self.backend,'execute',None)) or not callable(getattr(self.backend,'verify_terminal',None)): return False
        try:
            exact=hashlib.sha256(json.dumps(self.spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()==self.policy_hash
            approved=self.service.actions.db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='command_policy' AND version=? AND status='approved'",(self.policy_hash,)).fetchone()
            return bool(exact and approved)
        except Exception: return False
    def execute(self,run,queue_claim,graph_claim,expected):
        from runner.engineering_dispatch import workspace_snapshot
        if not self.ready(): raise CommandError('trusted command backend unavailable')
        service=self.service; graph=service.graph; db=graph.db
        if not service.queue.may_start(run,queue_claim): raise CommandError('queue claim unavailable')
        current=workspace_snapshot(service,run)
        if current!=expected: raise CommandError('workspace changed before command')
        assignment='job-'+secrets.token_hex(24); started=time.time()
        binding={key:current[key] for key in ('repository','base','branch','head','path','policy')}
        workspace_hash=hashlib.sha256(json.dumps(binding,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        db.execute('BEGIN IMMEDIATE')
        try:
            graph._lease(run,graph_claim)
            job=db.execute('SELECT status,token,expires FROM jobs WHERE run=?',(run,)).fetchone()
            if not job or job[0]!='running' or not secrets.compare_digest(job[1],queue_claim) or job[2]<=time.time(): raise CommandError('queue claim unavailable')
            if db.execute("SELECT 1 FROM supervised_commands WHERE run=? AND state='uncertain'",(run,)).fetchone(): raise CommandError('previous command outcome uncertain')
            if db.execute('UPDATE engineering_workspaces SET assignment=? WHERE run=? AND assignment IS NULL',(assignment,run)).rowcount!=1: raise CommandError('workspace already claimed')
            db.execute('INSERT INTO supervised_commands VALUES(?,?,?,?,?,?,NULL,?)',(assignment,run,current['head'],workspace_hash,self.policy_hash,'uncertain',started)); db.execute('COMMIT')
        except Exception: db.execute('ROLLBACK'); raise
        try:
            from runner.codex_executor import _bounded
            observation=_bounded(lambda:self.backend.execute(assignment=assignment,run=run,workspace=current['path'],commit=current['head'],argv=list(self.spec['argv']),timeout=self.spec['timeout_seconds'],output_limit=self.spec['output_limit']),time.monotonic()+self.spec['timeout_seconds'],lambda:not service.queue.may_start(run,queue_claim))
            if not isinstance(observation,dict) or set(observation)!={'outcome','exit_code','stdout','stderr'} or observation['outcome']!='exited' or type(observation['exit_code']) is not int or any(not isinstance(observation[k],bytes) or len(observation[k])>self.spec['output_limit'] for k in ('stdout','stderr')): raise CommandError('command outcome uncertain')
            if self.backend.verify_terminal(assignment,run,observation['exit_code']) is not True: raise CommandError('command termination evidence unavailable')
            after=workspace_snapshot(service,run,assignment=assignment)
            unchanged=after==current
            def redacted(data): return {'text':'[redacted]','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            record={'assignment':assignment,'run':run,'commit':current['head'],'workspace_hash':workspace_hash,'policy_hash':self.policy_hash,'argv':self.spec['argv'],'started':started,'finished':time.time(),'outcome':'exited','exit_code':observation['exit_code'],'stdout':redacted(observation['stdout']),'stderr':redacted(observation['stderr']),'workspace_unchanged':unchanged}
            ref=service.artifacts.put(json.dumps(record,sort_keys=True,separators=(',',':')).encode())
            db.execute('BEGIN IMMEDIATE')
            try:
                graph._lease(run,graph_claim)
                if not service.queue.may_start(run,queue_claim): raise CommandError('queue claim unavailable')
                if db.execute("UPDATE supervised_commands SET state=?,record_ref=? WHERE id=? AND state='uncertain'",('passed' if unchanged and observation['exit_code']==0 else 'failed',ref,assignment)).rowcount!=1: raise CommandError('command reservation changed')
                if db.execute('UPDATE engineering_workspaces SET assignment=NULL WHERE run=? AND assignment=?',(run,assignment)).rowcount!=1: raise CommandError('workspace claim changed')
                db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
            if not unchanged or observation['exit_code']!=0: raise CommandError('command failed or workspace changed')
            return ref
        except Exception as exc:
            state=db.execute('SELECT state FROM supervised_commands WHERE id=?',(assignment,)).fetchone()
            if state==('uncertain',):
                try: service.fence_execution(run)
                except Exception: pass
            if isinstance(exc,CommandError): raise
            raise CommandError('command outcome uncertain; inspection required') from None
    def verify(self,run,commit,ref,current):
        if not self.ready(): return False
        try:
            record=json.loads(self.service.artifacts.get(ref)); row=self.service.graph.db.execute('SELECT run,commit_id,workspace_hash,policy_hash,state,record_ref,started FROM supervised_commands WHERE id=?',(record['assignment'],)).fetchone()
            binding={key:current[key] for key in ('repository','base','branch','head','path','policy')}
            digest=hashlib.sha256(json.dumps(binding,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            return bool(row and row==(run,commit,digest,self.policy_hash,'passed',ref,record['started']) and record['run']==run and record['commit']==commit and record['workspace_hash']==digest and record['policy_hash']==self.policy_hash and record['argv']==self.spec['argv'] and record['outcome']=='exited' and record['exit_code']==0 and record['workspace_unchanged'] is True and record['finished']>=record['started'] and self.backend.verify_terminal(record['assignment'],run,record['exit_code']) is True)
        except Exception: return False
