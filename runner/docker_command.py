"""Run an approved test command in an exact disposable Engine container.

The controller must be separately authorized and isolated; this client is not
an Engine authorization boundary. Ambiguous operations remain fenced.
"""
import hashlib
import json
import sqlite3
import time
from runner.docker_controller import DockerAssignments, DockerController, DockerError

class DockerCommandBackend:
    def __init__(self,supervisor): self.supervisor=supervisor
    def verify_terminal(self,assignment,run,exit_code):
        db=sqlite3.connect(self.supervisor.db_path)
        try:
            row=db.execute("SELECT a.container_id FROM docker_execution_map m JOIN docker_assignments a ON a.run=m.assignment JOIN docker_terminal_evidence e ON e.run=a.run AND e.container_id=a.container_id WHERE m.assignment=? AND m.task_run=? AND m.role='test' AND a.state='terminated' AND e.status='exited'",(assignment,run)).fetchone()
        finally: db.close()
        if not row: return False
        try:
            _,state=DockerController(self.supervisor.assignments.engine,{assignment:row[0]})._inspect(assignment)
            return not state['Running'] and state['Status']=='exited' and type(state.get('ExitCode')) is int and state['ExitCode']==exit_code
        except Exception: return False

    def execute(self,*,assignment,run,workspace,commit,argv,timeout,output_limit):
        supervisor=self.supervisor; config=supervisor.config
        if config is None or not callable(supervisor.fence_ready) or supervisor.fence_ready(run) is not True: raise DockerError('manual execution fence unavailable')
        spec={'argv':argv,'timeout_seconds':timeout,'output_limit':output_limit}
        policy=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        db=sqlite3.connect(supervisor.db_path,timeout=10,isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute('SELECT run,commit_id,policy_hash,state FROM supervised_commands WHERE id=?',(assignment,)).fetchone()
                if row!=(run,commit,policy,'uncertain') or not db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='docker_config' AND version=? AND status='approved'",(config.policy_hash,)).fetchone() or not db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='command_policy' AND version=? AND status='approved'",(policy,)).fetchone(): raise DockerError('exact command approval unavailable')
                if db.execute("SELECT 1 FROM docker_execution_map m LEFT JOIN docker_assignments a ON a.run=m.assignment WHERE m.task_run=? AND (a.run IS NULL OR a.state!='terminated')",(run,)).fetchone(): raise DockerError('prior assignment unresolved')
                db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',(run,'test',assignment)); db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
            mounts=config.create_job_dirs(assignment); mounts['workspace']=workspace
            assignments=DockerAssignments(supervisor.db_path,supervisor.assignments.engine,config.image)
            try:
                cid=assignments.create_disposable(assignment,config,mounts,workspace_binding={'task':run,'role':'test'},command=spec)
                if supervisor.fence_ready(run) is not True: raise DockerError('task cancelled before command start')
                assignments.start(assignment,allow_completed=True)
                if supervisor.fence_ready(run) is not True: raise DockerError('task cancelled after command start')
                status,result=supervisor.assignments.engine.request('POST','/containers/'+cid+'/wait?condition=not-running')
                if status!=200 or not isinstance(result,dict) or type(result.get('StatusCode')) is not int or result.get('Error'): raise DockerError('command wait uncertain')
                _,state=DockerController(supervisor.assignments.engine,{assignment:cid})._inspect(assignment)
                if state['Running'] or state['Status']!='exited' or type(state.get('ExitCode')) is not int or state['ExitCode']!=result['StatusCode']: raise DockerError('command termination unverified')
                stdout,stderr=supervisor.assignments.engine.logs(cid,output_limit)
                db.execute('BEGIN IMMEDIATE')
                try:
                    if db.execute("UPDATE docker_assignments SET state='terminated' WHERE run=? AND container_id=? AND state='running'",(assignment,cid)).rowcount!=1: raise DockerError('assignment changed')
                    db.execute('INSERT INTO docker_terminal_evidence VALUES(?,?,?,?)',(assignment,cid,'exited',time.time())); db.execute('COMMIT')
                except Exception: db.execute('ROLLBACK'); raise
                return {'outcome':'exited','exit_code':result['StatusCode'],'stdout':stdout,'stderr':stderr}
            finally: assignments.close()
        finally: db.close()
