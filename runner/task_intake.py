"""Single-task intake; discovery is a trusted read-only integration."""
import datetime
import json
import time
from runner.plan_adapter import inspect_issue
from workflows.engineering.intake import readiness

class IntakeError(RuntimeError): pass

class SingleTaskIntake:
    def __init__(self,service,client,discover,policy,clock=datetime.date.today):
        if not callable(discover) or not isinstance(policy,dict): raise IntakeError('task discovery policy required')
        self.service,self.client,self.discover,self.policy,self.clock=service,client,discover,policy,clock
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)')
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_admission_audit(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, run TEXT NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL, reasons TEXT NOT NULL, artifact_ref TEXT, created REAL NOT NULL)')
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
        try: task=self.snapshot(workflow,project,number)
        except Exception:
            self.service.graph.db.execute('INSERT INTO task_admission_audit(schema_version,run,task_id,status,reasons,created) VALUES(1,?,?,?,?,?)',(run,'','unavailable',json.dumps(['task snapshot unavailable']),time.time()))
            raise
        issues=readiness(task,self.clock().isoformat())
        if issues:
            self.service.graph.db.execute('INSERT INTO task_admission_audit(schema_version,run,task_id,status,reasons,created) VALUES(1,?,?,?,?,?)',(run,task['task_id'],'blocked',json.dumps(issues),time.time()))
            raise IntakeError('task not ready: '+', '.join(issues))
        if not isinstance(task['source_version'],str) or not task['source_version']: raise IntakeError('source version unavailable')
        blob=json.dumps(task,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        ref=self.service.artifacts.put(blob)
        db=self.service.graph.db; definition=self.service.graph.definitions[workflow]
        db.execute('BEGIN IMMEDIATE')
        try:
            # Legacy display-only admissions cannot be safely assigned to a team.
            if db.execute('SELECT 1 FROM graph_runs WHERE task_id IN (?,?)',(task['task_id'],task['identifier'])).fetchone():
                db.execute('INSERT INTO task_admission_audit(schema_version,run,task_id,status,reasons,created) VALUES(1,?,?,?,?,?)',(run,task['task_id'],'rejected',json.dumps(['task already admitted']),time.time()))
                db.execute('COMMIT'); raise IntakeError('task already admitted')
            db.execute('INSERT INTO graph_runs VALUES(?,?,?,?,?)',(run,workflow,definition.fingerprint(),task['task_id'],'active'))
            db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',(run,task['source_version'],ref))
            seq=db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM jobs').fetchone()[0]
            db.execute("INSERT INTO jobs(run,status,seq) VALUES(?,'queued',?)",(run,seq))
            db.execute('INSERT INTO task_admission_audit(schema_version,run,task_id,status,reasons,artifact_ref,created) VALUES(1,?,?,?,?,?,?)',(run,task['task_id'],'admitted','[]',ref,time.time()))
            db.execute('COMMIT')
        except Exception:
            if db.in_transaction: db.execute('ROLLBACK')
            raise
        return {'run':run,'task':task['identifier'],'source_version':task['source_version'],'artifact_ref':ref}
