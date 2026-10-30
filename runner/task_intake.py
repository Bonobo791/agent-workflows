"""Single-task intake; discovery is a trusted read-only integration."""
import datetime
import json
from runner.plan_adapter import inspect_issue
from workflows.engineering.intake import readiness

class IntakeError(RuntimeError): pass

class SingleTaskIntake:
    def __init__(self,service,client,discover,policy,clock=datetime.date.today):
        if not callable(discover) or not isinstance(policy,dict): raise IntakeError('task discovery policy required')
        self.service,self.client,self.discover,self.policy,self.clock=service,client,discover,policy,clock
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)')
    def snapshot(self,workflow,project,number):
        existing=self.discover(project,number)
        if not isinstance(existing,list): raise IntakeError('existing-work discovery incomplete')
        task=inspect_issue(self.client,project,number,ready_columns=self.policy['ready_columns'],completed_columns=self.policy['completed_columns'],blocker_direction=self.policy['blocker_direction'],external_work=existing,today=self.clock().isoformat())
        if workflow=='marketing':
            value=self.policy.get('task_format')
            if not isinstance(value,str) or not value.strip(): raise IntakeError('marketing task format required')
            task['format']=value
        return task

    def submit(self,run,workflow,project,number):
        if workflow not in ('engineering','marketing') or not isinstance(run,str) or not run: raise IntakeError('invalid task selection')
        existing=self.discover(project,number)
        if not isinstance(existing,list): raise IntakeError('existing-work discovery incomplete')
        task=inspect_issue(self.client,project,number,ready_columns=self.policy['ready_columns'],completed_columns=self.policy['completed_columns'],blocker_direction=self.policy['blocker_direction'],external_work=existing,today=self.clock().isoformat())
        if workflow=='marketing':
            value=self.policy.get('task_format')
            if not isinstance(value,str) or not value.strip(): raise IntakeError('marketing task format required')
            task['format']=value
        issues=readiness(task,self.clock().isoformat())
        if issues: raise IntakeError('task not ready: '+', '.join(issues))
        if not isinstance(task['source_version'],str) or not task['source_version']: raise IntakeError('source version unavailable')
        blob=json.dumps(task,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        ref=self.service.artifacts.put(blob)
        db=self.service.graph.db; definition=self.service.graph.definitions[workflow]
        db.execute('BEGIN IMMEDIATE')
        try:
            if db.execute('SELECT 1 FROM graph_runs WHERE task_id=?',(task['task_id'],)).fetchone(): raise IntakeError('task already admitted')
            db.execute('INSERT INTO graph_runs VALUES(?,?,?,?,?)',(run,workflow,definition.fingerprint(),task['task_id'],'active'))
            db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',(run,task['source_version'],ref))
            seq=db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM jobs').fetchone()[0]
            db.execute("INSERT INTO jobs(run,status,seq) VALUES(?,'queued',?)",(run,seq))
            db.execute('COMMIT')
        except Exception: db.execute('ROLLBACK'); raise
        return {'run':run,'task':task['task_id'],'source_version':task['source_version'],'artifact_ref':ref}
