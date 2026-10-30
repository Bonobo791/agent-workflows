import os
import tempfile
import unittest
from pathlib import Path
from runner.worker import read_secret, ConfigurationError, build_control

class WorkerTests(unittest.TestCase):
    def test_bootstrap_wires_trusted_lifecycle_and_rejects_live_mode(self):
        class FakeLifecycle:
            target={'origin':'https://example.test','service':'svc','application':'app'}
            policy_hash='fixture'
            auto=False
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ConfigurationError):
                build_control(root,'x'*40,execution_mode='live')
            service,control=build_control(root,'x'*40,lifecycle_factory=lambda service: FakeLifecycle())
            try:
                config=control.handle('x'*40,{'action':'lifecycle_config'})
                self.assertTrue(config['manual_available'])
                self.assertFalse(config['automatic'])
            finally: service.close()

    def test_secret_requires_private_file_and_sufficient_entropy_length(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'secret'; path.write_text('x'*40); os.chmod(path,0o600)
            self.assertEqual(read_secret(path),'x'*40)
            os.chmod(path,0o644)
            with self.assertRaises(ConfigurationError): read_secret(path)

    def test_worker_constructs_docker_assignments_without_connecting(self):
        with tempfile.TemporaryDirectory() as root:
            service,control=build_control(root,'x'*40,docker_socket='/tmp/not-connected.sock',docker_image='example@sha256:'+'1'*64)
            try:
                self.assertIsNotNone(service.docker_assignments)
                self.assertIsNotNone(service.docker_supervisor)
                self.assertIsNone(service.docker_assignments.get('unknown'))
            finally: service.close()

    def test_pr_actions_are_unavailable_without_trusted_authority(self):
        from runner.control import ControlError
        with tempfile.TemporaryDirectory() as root:
            service,control=build_control(root,'x'*40)
            try:
                with self.assertRaises(ControlError): control.handle('x'*40,{'action':'pr_propose','run':'r','queue_claim':'q','graph_claim':'g','kind':'push','parameters':{'expected_remote':None}})
            finally: service.close()

    def test_worker_rejects_incomplete_docker_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ConfigurationError,'Docker socket and immutable image'):
                build_control(root,'x'*40,docker_socket='/tmp/not-connected.sock')

    def test_configured_worker_constructs_executor_and_requires_exact_approval(self):
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            runtime={'socket':'/tmp/not-connected.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            from runner.manual_lifecycle import ManualLifecycle
            service,control=build_control(root,'x'*40,docker_config=runtime,lifecycle_factory=lambda service: ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                self.assertIsNotNone(service.docker_executor)
                self.assertIsNotNone(service.session_store)
                self.assertIsNotNone(service.docker_supervisor.credential_delivery)
                self.assertFalse(service.docker_supervisor.preflight())
                policy=service.docker_supervisor.config.policy_hash
                with self.assertRaises(Exception): service.approve('controller','docker_config','0'*64,'operator')
                service.approve('controller','docker_config',policy,'operator')
                self.assertFalse(service.docker_supervisor.preflight())
                service.docker_supervisor.provider_ready=lambda:True  # synthetic verifier only
                self.assertTrue(service.docker_supervisor.preflight())
            finally: service.lifecycle.close(); service.close()

    def test_runtime_config_file_is_private_json(self):
        import json
        from runner.worker import read_runtime_config
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'runtime.json'; path.write_text(json.dumps({'socket':'/fake/socket'})); path.chmod(0o600)
            self.assertEqual(read_runtime_config(path),{'socket':'/fake/socket'})
            path.chmod(0o644)
            with self.assertRaises(ConfigurationError): read_runtime_config(path)

    def test_configured_executor_requires_manual_fence(self):
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            runtime={'socket':'/tmp/not-connected.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            service,control=build_control(root,'x'*40,docker_config=runtime)
            try:
                service.approve('controller','docker_config',service.docker_supervisor.config.policy_hash,'operator')
                self.assertFalse(service.docker_supervisor.preflight())
            finally: service.close()

    def test_control_requires_exact_approval_before_disposable_escalation(self):
        from runner.control import ControlError
        cid='a'*64
        class Engine:
            def __init__(self): self.running=True; self.calls=[]
            def request(self,method,path,body=None):
                self.calls.append((method,path))
                if path.endswith('/json'): return 200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run':'job-1'}},'HostConfig':{'RestartPolicy':{'Name':'no'},'AutoRemove':False},'State':{'Running':self.running,'Status':'running' if self.running else 'exited'}}
                if '/kill?' in path: self.running=False; return 204,None
                if '/wait?' in path: return 200,{'StatusCode':137}
                raise AssertionError(path)
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            runtime={'socket':'/tmp/not-connected.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            service,control=build_control(root,'x'*40,docker_config=runtime)
            try:
                engine=Engine(); service.docker_assignments.engine=engine
                policy=service.docker_supervisor.config.policy_hash
                service.graph.db.execute('INSERT INTO docker_assignments VALUES(?,?,?,?)',('job-1',cid,'stopping',runtime['image']))
                service.graph.db.execute('INSERT INTO docker_assignment_policy VALUES(?,?)',('job-1',policy))
                service.graph.db.execute('INSERT INTO docker_disposable_assignments VALUES(?,?,?)',('job-1',cid,policy))
                version=service.docker_supervisor.escalation_version('job-1',policy)
                with self.assertRaises(ControlError): control.handle('x'*40,{'action':'docker_escalate','run':'job-1'})
                self.assertFalse(any('/kill?' in path for _,path in engine.calls))
                control.handle('x'*40,{'action':'approve','run':'job-1','approval':'docker_kill','version':version})
                self.assertTrue(control.handle('x'*40,{'action':'docker_escalate','run':'job-1'})['terminated'])
            finally: service.close()

    def test_manual_stop_attestation_cannot_release_uncertain_docker_launch(self):
        from runner.manual_lifecycle import ManualLifecycle
        from runner.control import ControlError
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            runtime={'socket':'/tmp/not-connected.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            service,control=build_control(root,'x'*40,docker_config=runtime,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.create('run','marketing','task'); service.enqueue('run'); service.lifecycle.claim()
                service.graph.db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',('run','implement','job-1'))
                service.graph.db.execute('INSERT INTO docker_assignments VALUES(?,?,?,?)',('job-1','a'*64,'starting',runtime['image']))
                service.request_lifecycle_stop('run')
                with self.assertRaises(ControlError): control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run','incarnation':'inc','attestation':'terminated_and_fenced'})
                self.assertEqual(service.queue.get('run')['status'],'cancelling')
                service.graph.db.execute("UPDATE docker_assignments SET state='terminated' WHERE run='job-1'")
                service.graph.db.execute('INSERT INTO docker_terminal_evidence VALUES(?,?,?,?)',('job-1','a'*64,'exited',1))
                self.assertEqual(control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run','incarnation':'inc','attestation':'terminated_and_fenced'})['status'],'stopped')
            finally: service.lifecycle.close(); service.close()

    def test_manual_stop_reports_exact_unresolved_container(self):
        from runner.manual_lifecycle import ManualLifecycle
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            runtime={'socket':'/tmp/not-connected.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            service,control=build_control(root,'x'*40,docker_config=runtime,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.create('run','marketing','task'); service.enqueue('run'); service.lifecycle.claim()
                service.graph.db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',('run','implement','job-1'))
                service.graph.db.execute('INSERT INTO docker_assignments VALUES(?,?,?,?)',('job-1','a'*64,'running',runtime['image']))
                result=control.handle('x'*40,{'action':'manual_stop','run':'run'})
                self.assertEqual(result['docker_assignments'],[{'assignment':'job-1','container_id':'a'*64,'state':'running'}])
            finally: service.lifecycle.close(); service.close()

    def test_configured_executor_rejects_undiscovered_or_different_model(self):
        from runner.model_policy import ModelPolicyError
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            runtime={'socket':'/tmp/not-connected.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            service,control=build_control(root,'x'*40,docker_config=runtime)
            try:
                self.assertIsNotNone(service.model_policy)
                with self.assertRaises(ModelPolicyError): service.docker_executor.execute(run='r',role='qa',claim='c',skills={},prompt='p',model='other',input_versions=[],output_paths=[])
                service.model_policy.discover(lambda *_:{'data':[{'model':'discovered-terra','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None})
                with self.assertRaises(ModelPolicyError): service.docker_executor.execute(run='r',role='qa',claim='c',skills={},prompt='p',model='other',input_versions=[],output_paths=[])
            finally: service.close()

    def test_approved_worker_still_requires_verified_chatgpt_provider(self):
        from runner.manual_lifecycle import ManualLifecycle
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            runtime={'socket':'/tmp/not-connected.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            service,control=build_control(root,'x'*40,docker_config=runtime,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.approve('controller','docker_config',service.docker_supervisor.config.policy_hash,'operator')
                self.assertFalse(service.docker_supervisor.preflight())
            finally: service.lifecycle.close(); service.close()

    def test_startup_constructs_one_configured_task_client(self):
        import json
        from unittest.mock import patch
        config={'run':'run-1','workflow':'engineering','project':'DEMO','number':1,'ready_columns':[10],'completed_columns':[20],'blocker_direction':'inward','plan_endpoint':'https://plan.example.invalid/mcp','github_owner':'owner','github_repo':'repo','github_branch':'task-1'}
        class ProtocolTransport:
            def post(self,message,headers):
                if message['method']=='initialize': result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}}}
                elif message['method']=='notifications/initialized': return 202,{},b''
                else:
                    name=message['params']['name']
                    if name=='get_issue_by_number': data={'id':1,'identifier':'DEMO-1','title':'Synthetic','description':'Example','columnId':10,'archivedAt':None,'assigneeUserId':None,'delegateUserId':None,'dueDate':None,'startDate':None,'subtasks':[],'links':[],'development':[],'updatedAt':'v1'}
                    elif name=='list_issue_activity': data={'items':[],'nextCursor':None}
                    else: data=[]
                    result={'structuredContent':{'ok':True,'data':data}}
                return 200,{'Content-Type':'application/json'},json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}).encode()
        class GitHub:
            def request(self,method,path,body=None): return []
        with tempfile.TemporaryDirectory() as root, patch('runner.plan_mcp.HTTPSMCP',return_value=ProtocolTransport()), patch('workflows.engineering.github.HTTPSGitHub',return_value=GitHub()):
            service,control=build_control(root,'x'*40,task_config=config,plan_token='synthetic-plan',github_token='synthetic-github')
            try:
                result=control.handle('x'*40,{'action':'submit_configured'})
                self.assertEqual(result['task'],'DEMO-1'); self.assertEqual(service.queue.get('run-1')['status'],'queued')
            finally: service.close()

    def test_live_mode_explains_missing_credential_delivery(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ConfigurationError,'credential delivery'):
                build_control(root,'x'*40,execution_mode='live')

    def test_startup_loads_explicit_role_skills_from_private_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            skills=Path(root)/'skills'; (skills/'planner').mkdir(parents=True); (skills/'planner'/'SKILL.md').write_text('Synthetic')
            config={'skill_roots':[str(skills)],'required_skills':{'plan':['planner']}}
            service,control=build_control(root,'x'*40,role_config=config)
            try:
                self.assertEqual(service.required_skills['plan'],['planner'])
                self.assertEqual(service.skill_resolver.resolve(['planner'])['planner']['content'],'Synthetic')
            finally: service.close()
