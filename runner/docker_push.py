"""Exact approved Git push in a dedicated disposable Engine container.

A real credential-delivery provider is deliberately not supplied. Client-side
checks are not a substitute for daemon authorization or host network policy.
"""
import json
import re
import secrets
import sqlite3
import subprocess
import time
from runner.docker_controller import DockerAssignments, DockerController, DockerError
from runner.engineering_workspace import EngineeringWorkspace
from runner.engineering_dispatch import workspace_snapshot
from workflows.engineering.github import GitHub

def push_argv(repo,branch,head,expected_remote):
    if not isinstance(repo,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo): raise DockerError('invalid repository')
    GitHub._branch(branch); GitHub._sha(head)
    if expected_remote is not None: GitHub._sha(expected_remote)
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',head) or (expected_remote is not None and not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',expected_remote)): raise DockerError('full object IDs required')
    return ['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-c','credential.helper=','push','--porcelain','--force-with-lease=refs/heads/'+branch+':'+(expected_remote or ''),'--','https://github.com/'+repo+'.git',head+':refs/heads/'+branch]

def require_fast_forward(path,expected_remote,head):
    if expected_remote is None: return
    try:
        result=subprocess.run(['git','-C',path,'merge-base','--is-ancestor',expected_remote,head],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_NO_REPLACE_OBJECTS':'1'})
    except (OSError,subprocess.TimeoutExpired): raise DockerError('ancestry verification unavailable') from None
    if result.returncode!=0: raise DockerError('approved push is not a verified fast-forward')

class DockerPushTransport:
    def __init__(self,service,run,queue_claim,graph_claim,credentials):
        self.service=service; self.run=run; self.queue_claim=queue_claim; self.graph_claim=graph_claim; self.credentials=credentials
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS git_push_attempts(assignment TEXT PRIMARY KEY,task_run TEXT NOT NULL,repo TEXT NOT NULL,branch TEXT NOT NULL,head TEXT NOT NULL,action TEXT NOT NULL,state TEXT NOT NULL)')
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS pr_mutation_processes(assignment TEXT PRIMARY KEY,run TEXT NOT NULL,action TEXT NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL)')
    def push_exact(self,branch,head):
        service=self.service; supervisor=service.docker_supervisor; config=supervisor.config
        if config is None or config.network=='none' or not callable(getattr(self.credentials,'ready',None)) or self.credentials.ready() is not True or not service.queue.may_start(self.run,self.queue_claim) or supervisor.fence_ready(self.run) is not True: raise DockerError('approved isolated push boundary unavailable')
        service.graph._lease(self.run,self.graph_claim)
        workspace=workspace_snapshot(service,self.run)
        if workspace['branch']!=branch or workspace['head']!=head: raise DockerError('push target changed')
        repo=workspace['repository']; scope='github:'+repo+':branch:'+branch
        assignment='job-'+secrets.token_hex(24); db=service.graph.db
        db.execute('BEGIN IMMEDIATE')
        try:
            service.graph._lease(self.run,self.graph_claim)
            if not service.queue.may_start(self.run,self.queue_claim): raise DockerError('task claim unavailable')
            rows=db.execute("SELECT a.action,b.manifest FROM external_actions a JOIN action_scopes s ON s.run=a.run AND s.action=a.action AND s.version=a.version JOIN action_bindings b ON b.run=a.run AND b.action=a.action AND b.version=a.version WHERE a.run=? AND a.version=? AND a.status='uncertain' AND a.action LIKE 'push:%' AND s.scope=?",(self.run,head,scope)).fetchall()
            if len(rows)!=1: raise DockerError('scoped push approval unavailable')
            action,manifest=rows[0]; target=json.loads(manifest)
            refs=db.execute("SELECT proposal FROM pr_bridge_operations WHERE run=? AND kind='push' AND state IN ('approved','uncertain')",(self.run,)).fetchall()
            proposals=[json.loads(service.artifacts.get(ref)) for (ref,) in refs]
            matches=[p for p in proposals if p.get('action')==action and p.get('target')==target]
            if len(matches)!=1 or set(matches[0].get('args',{}))!={'expected_remote'}: raise DockerError('exact approved push proposal unavailable')
            expected_remote=matches[0]['args']['expected_remote']; push_argv(repo,branch,head,expected_remote)
            require_fast_forward(workspace['path'],expected_remote,head)
            if target.get('repo')!=repo or target.get('branch')!=branch or target.get('head')!=head or target.get('diff')!=__import__('hashlib').sha256(workspace['diff'].encode()).hexdigest(): raise DockerError('approved push target changed')
            if db.execute("SELECT 1 FROM docker_execution_map m LEFT JOIN docker_assignments a ON a.run=m.assignment WHERE m.task_run=? AND (a.run IS NULL OR a.state!='terminated')",(self.run,)).fetchone(): raise DockerError('prior execution unresolved')
            db.execute('INSERT INTO git_push_attempts VALUES(?,?,?,?,?,?,?)',(assignment,self.run,repo,branch,head,action,'reserved'))
            db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',(self.run,'push',assignment))
            db.execute('INSERT INTO pr_mutation_processes VALUES(?,?,?,?,?)',(assignment,self.run,action,scope,'push')); db.execute('COMMIT')
        except Exception: db.execute('ROLLBACK'); raise
        registry=EngineeringWorkspace(config.job_root.parent)
        try: path=registry.claim(self.run,assignment,config.policy_hash)
        finally: registry.close()
        if path!=workspace['path']: raise DockerError('workspace changed')
        mounts=config.create_job_dirs(assignment); mounts['workspace']=path
        assignments=DockerAssignments(supervisor.db_path,supervisor.assignments.engine,config.image)
        try:
            cid=assignments.create_disposable(assignment,config,mounts,workspace_binding={'task':self.run,'role':'push'},push={'repo':repo,'branch':branch,'head':head,'action':action,'expected_remote':expected_remote})
            if self.credentials.deliver(assignment,cid) is not True: raise DockerError('credential boundary unavailable')
            if supervisor.fence_ready(self.run) is not True: raise DockerError('task cancelled before push')
            assignments.start(assignment,allow_completed=True)
            if supervisor.fence_ready(self.run) is not True: raise DockerError('task cancelled after push start')
            status,result=supervisor.assignments.engine.request('POST','/containers/'+cid+'/wait?condition=not-running')
            if status!=200 or not isinstance(result,dict) or type(result.get('StatusCode')) is not int or result.get('Error'): raise DockerError('push outcome uncertain')
            _,state=DockerController(supervisor.assignments.engine,{assignment:cid})._inspect(assignment)
            if state['Running'] or state['Status']!='exited' or type(state.get('ExitCode')) is not int or state['ExitCode']!=result['StatusCode']: raise DockerError('push process termination unverified')
            db.execute('BEGIN IMMEDIATE')
            try:
                if db.execute("UPDATE docker_assignments SET state='terminated' WHERE run=? AND container_id=? AND state='running'",(assignment,cid)).rowcount!=1: raise DockerError('assignment changed')
                db.execute('INSERT INTO docker_terminal_evidence VALUES(?,?,?,?)',(assignment,cid,'exited',time.time()))
                db.execute("UPDATE git_push_attempts SET state='terminated' WHERE assignment=? AND state='reserved'",(assignment,)); db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
            registry=EngineeringWorkspace(config.job_root.parent)
            try: registry.release(self.run,assignment)
            finally: registry.close()
            if workspace_snapshot(service,self.run)!=workspace: raise DockerError('workspace changed during push; inspect remote outcome')
            if result['StatusCode']!=0: raise DockerError('push failed; inspect remote outcome')
            return True
        finally: assignments.close()
