import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from runner.worker import read_secret, ConfigurationError, build_control

class WorkerTests(unittest.TestCase):
    def test_local_task_can_use_signed_in_tracker_gateway_without_token(self):
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import SystemdScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.plan_codex import CodexAppsPlan
        class ReadOnly:
            def request(self,method,path,body=None): return []
        task={'run':'run','workflow':'engineering','project':'DEMO','number':1,'ready_columns':[1],'completed_columns':[2],'blocker_direction':'inward','plan_endpoint':'codex-apps','github_owner':'owner','github_repo':'repo','github_branch':'task-1'}
        with tempfile.TemporaryDirectory() as root:
            factory=lambda service:LocalSupervisor(root+'/state.db','/unused',{'HOME':root},SystemdScopes({}),lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]})
            options={'execution_mode':'local','local_supervisor_factory':factory,'lifecycle_factory':lambda _:ManualLifecycle(root+'/state.db','worker','inc'),'task_config':task,'github_http':ReadOnly()}
            service,_=build_control(root,'x'*40,**options)
            try:self.assertIsInstance(service.task_intake.intake.client,CodexAppsPlan)
            finally:service.lifecycle.close();service.close()
            with self.assertRaises(ConfigurationError):build_control(root,'x'*40,**{**options,'plan_token':'unexpected'})
            with self.assertRaises(ConfigurationError):build_control(root,'x'*40,task_config=task,github_token='fixture')

    def test_gateway_rejects_token_file_before_reading_it(self):
        from unittest.mock import patch
        from runner.worker import run_worker
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'WORKFLOW_TASK_CONFIG_FILE':'task','PLAN_TOKEN_FILE':'secret'},clear=True), patch('runner.worker.read_runtime_config',return_value={'plan_endpoint':'codex-apps'}), patch('runner.worker.read_secret') as read:
            with self.assertRaises(ConfigurationError): run_worker(Path(root),'x'*40)
            read.assert_not_called()

    def test_local_execution_requires_explicit_supervisor_and_manual_lifecycle(self):
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import SystemdScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.worker import build_control, ConfigurationError
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ConfigurationError): build_control(root,'x'*40,execution_mode='local')
            factory=lambda service: LocalSupervisor(root+'/state.db','/unused',{'HOME':root},SystemdScopes({}),lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]})
            with self.assertRaises(ConfigurationError): build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory)
            command={'argv':['/usr/bin/true'],'timeout_seconds':5,'output_limit':1024}
            service,_=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'),command_config=command)
            try:
                self.assertIsInstance(service.model_supervisor,LocalSupervisor)
                self.assertIs(service.model_executor.supervisor,service.model_supervisor)
                self.assertFalse(service.model_supervisor.fence('missing'))
                from runner.local_command import LocalCommandBackend
                self.assertIsInstance(service.command_supervisor.backend,LocalCommandBackend)
                self.assertFalse(service.command_supervisor.ready())
            finally: service.lifecycle.close(); service.close()
    def test_local_task_can_use_read_only_github_client_without_token(self):
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import SystemdScopes
        from runner.manual_lifecycle import ManualLifecycle
        class ReadOnly:
            def request(self,method,path,body=None):return []
        task={'run':'run','workflow':'engineering','project':'DEMO','number':1,'ready_columns':[1],'completed_columns':[2],'blocker_direction':'inward','plan_endpoint':'https://plan.example.invalid/mcp','github_owner':'owner','github_repo':'repo','github_branch':'task-1'}
        with tempfile.TemporaryDirectory() as root:
            factory=lambda service:LocalSupervisor(root+'/state.db','/unused',{'HOME':root},SystemdScopes({}),lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]})
            client=ReadOnly()
            service,_=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'),task_config=task,plan_token='synthetic',github_http=client)
            try:self.assertIs(service.task_intake.github,client)
            finally:service.lifecycle.close();service.close()
    def test_local_podman_configuration_constructs_render_authority(self):
        from unittest.mock import patch
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import SystemdScopes
        from runner.manual_lifecycle import ManualLifecycle
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs';jobs.mkdir(mode=0o700)
            runtime={'socket':'/tmp/engine.sock','image':'sha256:'+'1'*64,'network':'none','job_root':str(jobs),'engine':'podman-rootless'}
            factory=lambda service:LocalSupervisor(root+'/state.db','/unused',{'HOME':root},SystemdScopes({}),lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]},policy_hash='a'*64)
            with patch('runner.podman_policy.PodmanRenderPolicy') as policy:
                service,_=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,docker_config=runtime,lifecycle_factory=lambda _:ManualLifecycle(root+'/state.db','worker','inc'))
                try:
                    self.assertIs(service.openfilm_backend.verify_policy,policy.return_value)
                    self.assertIs(service.openfilm_backend.builder.supervisor,service.model_supervisor)
                finally:service.lifecycle.close();service.close()
    def test_local_asset_review_is_wired_when_role_is_configured(self):
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import SystemdScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.local_asset_review import LocalAssetReviewer
        with tempfile.TemporaryDirectory() as root:
            skills=Path(root)/'skills';skills.mkdir()
            factory=lambda service:LocalSupervisor(root+'/state.db','/unused',{'HOME':root},SystemdScopes({}),lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]},policy_hash='a'*64)
            service,_=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,lifecycle_factory=lambda _:ManualLifecycle(root+'/state.db','worker','inc'),role_config={'skill_roots':[str(skills)],'required_skills':{'asset_review':['review']}})
            try:self.assertIsInstance(service.openfilm_reviewer,LocalAssetReviewer)
            finally:service.lifecycle.close();service.close()
    def test_detached_fence_uses_thread_local_connections(self):
        import threading
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import SystemdScopes
        from runner.manual_lifecycle import ManualLifecycle
        with tempfile.TemporaryDirectory() as root:
            factory=lambda service:LocalSupervisor(root+'/state.db','/unused',{'HOME':root},SystemdScopes({}),lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]})
            service,_=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,lifecycle_factory=lambda _:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.create('run','marketing','task');service.enqueue('run');service.lifecycle.claim()
                results=[]
                def stop():
                    try: results.append(service.fence_execution_detached('run'))
                    except Exception as error: results.append(error)
                thread=threading.Thread(target=stop);thread.start();thread.join(timeout=5)
                self.assertFalse(thread.is_alive());self.assertEqual(results[0]['incarnation'],'inc')
                self.assertEqual(service.lifecycle.status('run')['status'],'pending')
                self.assertEqual(service.queue.get('run')['status'],'cancelling')
            finally:service.lifecycle.close();service.close()
    def test_local_github_cli_enables_only_claim_scoped_pr_authority(self):
        from unittest.mock import patch
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import SystemdScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.gh_read import GHRead
        task={'run':'run','workflow':'engineering','project':'DEMO','number':1,'ready_columns':[1],'completed_columns':[2],'blocker_direction':'inward','plan_endpoint':'https://plan.example.invalid/mcp','github_owner':'owner','github_repo':'repo','github_branch':'task-1'}
        with tempfile.TemporaryDirectory() as root:
            gh=Path(root)/'gh';gh.write_text('#!/bin/sh\nexit 0\n');gh.chmod(0o700);client=GHRead(str(gh),{'HOME':root})
            factory=lambda service:LocalSupervisor(root+'/state.db','/unused',{'HOME':root},SystemdScopes({}),lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]})
            service,_=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'),task_config=task,plan_token='synthetic',github_http=client)
            try:
                with patch.object(service.model_supervisor,'preflight',return_value=True):
                    bound=service.pr_authority.for_claim('run','q','g')
                    self.assertTrue(all(bound.supports(kind) for kind in ('push','draft','ready')))
                    self.assertFalse(bound.supports('merge'))
            finally:service.lifecycle.close();service.close()
    def test_reddit_context_requires_an_explicit_trusted_factory(self):
        class Authority:
            def check(self,*args): return 'fixture'
            def verify(self,*args): return True
        with tempfile.TemporaryDirectory() as root:
            service,_=build_control(root,'x'*40,reddit_context_factory=lambda _:Authority())
            try: self.assertIsInstance(service.reddit_context_authority,Authority)
            finally: service.close()
            with self.assertRaises(ConfigurationError): build_control(root,'x'*40,reddit_context_factory=lambda _:object())
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
                service.graph.db.execute("CREATE TRIGGER reject_manual_audit BEFORE INSERT ON manual_lifecycle_audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
                with self.assertRaises(sqlite3.IntegrityError): service.lifecycle.confirm_operator_stop('run','inc','terminated_and_fenced')
                self.assertEqual(service.queue.get('run')['status'],'cancelling')
                self.assertEqual(service.lifecycle.status('run')['status'],'pending')
                service.graph.db.execute('DROP TRIGGER reject_manual_audit')
                self.assertEqual(control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run','incarnation':'inc','attestation':'terminated_and_fenced'})['status'],'stopped')
                audit=service.graph.db.execute('SELECT state,confirmation FROM manual_lifecycle_audit WHERE run=? ORDER BY id',('run',)).fetchall()
                self.assertEqual(audit,[('running',None),('pending',None),('stopped','operator-attested')])
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

    def test_manual_stop_cannot_release_unresolved_local_scope(self):
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import SystemdScopes, LocalScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.control import ControlError
        with tempfile.TemporaryDirectory() as root:
            factory=lambda service: LocalSupervisor(root+'/state.db','/unused',{'HOME':root},SystemdScopes({}),lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]})
            service,control=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.create('run','marketing','task'); service.enqueue('run'); service.lifecycle.claim()
                scopes=LocalScopes(root+'/state.db',SystemdScopes({}));unit=scopes.reserve('run');scopes.close()
                result=control.handle('x'*40,{'action':'manual_stop','run':'run'})
                self.assertEqual(result['local_scopes'],[{'unit':unit,'state':'starting'}])
                with self.assertRaises(ControlError): control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run','incarnation':'inc','attestation':'terminated_and_fenced'})
                self.assertEqual(service.queue.get('run')['status'],'cancelling')
            finally: service.lifecycle.close(); service.close()

    def test_fenced_active_local_scope_can_be_stopped_but_unstarted_cannot(self):
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import LocalScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.control import ControlError
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            backend=Backend()
            factory=lambda service: LocalSupervisor(root+'/state.db','/unused',{'HOME':root},backend,lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]})
            service,control=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.create('run','marketing','task'); service.enqueue('run'); service.lifecycle.claim()
                scopes=LocalScopes(root+'/state.db',backend); unit=scopes.reserve('run')
                with self.assertRaises(ControlError): control.handle('x'*40,{'action':'stop_local_scope','run':'run','unit':unit})
                control.handle('x'*40,{'action':'manual_stop','run':'run'})
                with self.assertRaises(ControlError): control.handle('x'*40,{'action':'stop_local_scope','run':'run','unit':unit})
                self.assertEqual(scopes.state('run',unit),'starting')
                backend.unit=unit
                self.assertEqual(control.handle('x'*40,{'action':'stop_local_scope','run':'run','unit':unit})['state'],'terminated')
                self.assertEqual(control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run','incarnation':'inc','attestation':'terminated_and_fenced'})['status'],'stopped')
                scopes.close()
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
