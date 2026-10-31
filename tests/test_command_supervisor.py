import hashlib
import json
from runner.command_supervisor import CommandError, CommandSupervisor
from runner.engineering_dispatch import workspace_snapshot
from tests.test_engineering_workspace import EngineeringWorkspaceTests, git

class CommandSupervisorTests(EngineeringWorkspaceTests):
    def prepared(self,backend):
        service=self.service; service.create('run-1','engineering','task'); service.enqueue('run-1')
        queue=self.control.handle('x'*40,{'action':'manual_claim'})['claim']; claim=self.control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        spec={'argv':['/usr/bin/python3','-c','print("fixture")'],'timeout_seconds':5,'output_limit':1024}
        policy=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if not hasattr(backend,'verify_terminal'): backend.verify_terminal=lambda assignment,run,exit_code: True
        supervisor=CommandSupervisor(service,spec,policy,backend); service.command_supervisor=supervisor; service.approve('controller','command_policy',policy,'operator')
        return supervisor,queue['token'],claim,workspace_snapshot(service,'run-1')
    def test_valid_observation_and_forged_role_report(self):
        class Backend:
            def execute(self,**kw): return {'outcome':'exited','exit_code':0,'stdout':b'fixture secret output','stderr':b''}
        supervisor,queue,claim,snapshot=self.prepared(Backend())
        forged=self.service.artifacts.put(json.dumps({'commands':[{'exit_code':0}]}).encode())
        self.assertFalse(supervisor.verify('run-1',snapshot['head'],forged,snapshot))
        ref=supervisor.execute('run-1',queue,claim,snapshot)
        history=self.service.graph.db.execute('SELECT status,record_ref FROM command_audit ORDER BY id').fetchall()
        self.assertEqual(history,[('uncertain',None),('passed',ref)])
        self.assertTrue(supervisor.verify('run-1',snapshot['head'],ref,snapshot))
        record=json.loads(self.service.artifacts.get(ref)); self.assertEqual(record['stdout']['text'],'[redacted]'); self.assertNotIn('secret',json.dumps(record))
        self.assertFalse(supervisor.verify('run-1','0'*40,ref,snapshot))
        supervisor.spec['argv'].append('injected'); self.assertFalse(supervisor.ready()); self.assertFalse(supervisor.verify('run-1',snapshot['head'],ref,snapshot))
    def test_definite_failure_releases_workspace_but_cannot_pass(self):
        class Backend:
            def execute(self,**kw): return {'outcome':'exited','exit_code':1,'stdout':b'', 'stderr':b'failed'}
        supervisor,queue,claim,snapshot=self.prepared(Backend())
        with self.assertRaises(CommandError): supervisor.execute('run-1',queue,claim,snapshot)
        row=self.service.graph.db.execute('SELECT state,record_ref FROM supervised_commands').fetchone()
        self.assertEqual(row[0],'failed'); self.assertFalse(supervisor.verify('run-1',snapshot['head'],row[1],snapshot))
        self.assertIsNone(self.registry.db.execute('SELECT assignment FROM engineering_workspaces').fetchone()[0])
    def test_timeout_fences_workspace_and_retry(self):
        class Backend:
            def execute(self,**kw): raise TimeoutError('unknown')
        supervisor,queue,claim,snapshot=self.prepared(Backend())
        with self.assertRaises(CommandError): supervisor.execute('run-1',queue,claim,snapshot)
        self.assertEqual(self.service.graph.db.execute('SELECT state FROM supervised_commands').fetchone()[0],'uncertain')
        self.assertEqual(self.service.graph.db.execute('SELECT status FROM command_audit').fetchall(),[('uncertain',)])
        self.assertIsNotNone(self.registry.db.execute('SELECT assignment FROM engineering_workspaces').fetchone()[0])
        with self.assertRaises(Exception): supervisor.execute('run-1',queue,claim,snapshot)
    def test_audit_failure_fences_completed_command(self):
        class Backend:
            def execute(self,**kw): return {'outcome':'exited','exit_code':0,'stdout':b'', 'stderr':b''}
        supervisor,queue,claim,snapshot=self.prepared(Backend())
        self.service.graph.db.execute("CREATE TRIGGER reject_command_audit BEFORE INSERT ON command_audit WHEN NEW.status='passed' BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
        with self.assertRaises(CommandError): supervisor.execute('run-1',queue,claim,snapshot)
        self.assertEqual(self.service.graph.db.execute('SELECT state FROM supervised_commands').fetchone(),('uncertain',))
        self.assertIsNotNone(self.registry.db.execute('SELECT assignment FROM engineering_workspaces').fetchone()[0])
    def test_late_backend_completion_does_not_release_fenced_workspace(self):
        import threading
        gate=threading.Event(); finished=threading.Event()
        class Backend:
            def execute(self,**kw):
                gate.wait(5); finished.set()
                return {'outcome':'exited','exit_code':0,'stdout':b'', 'stderr':b''}
        supervisor,queue,claim,snapshot=self.prepared(Backend())
        supervisor.spec['timeout_seconds']=1
        supervisor.policy_hash=hashlib.sha256(json.dumps(supervisor.spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.service.approve('controller','command_policy',supervisor.policy_hash,'operator')
        with self.assertRaises(CommandError): supervisor.execute('run-1',queue,claim,snapshot)
        gate.set(); self.assertTrue(finished.wait(2))
        self.assertEqual(self.service.graph.db.execute('SELECT state FROM supervised_commands').fetchone()[0],'uncertain')
        self.assertIsNotNone(self.registry.db.execute('SELECT assignment FROM engineering_workspaces').fetchone()[0])

    def test_changed_commit_cannot_be_approved(self):
        repo=self.repo
        class Backend:
            def execute(self,**kw):
                (repo/'changed').write_text('changed'); git(repo,'add','changed'); git(repo,'commit','-m','changed')
                return {'outcome':'exited','exit_code':0,'stdout':b'', 'stderr':b''}
        supervisor,queue,claim,snapshot=self.prepared(Backend())
        with self.assertRaises(CommandError): supervisor.execute('run-1',queue,claim,snapshot)
        self.assertEqual(self.service.graph.db.execute('SELECT state FROM supervised_commands').fetchone()[0],'failed')
