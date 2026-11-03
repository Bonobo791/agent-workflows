import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from runner.worker import read_secret, ConfigurationError, build_control

class WorkerTests(unittest.TestCase):
    def test_local_task_can_use_signed_in_tracker_gateway_without_token(self):
        """Accept either read-only GitHub credentials path and reject mixed tracker credentials."""
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
            token_options={key:value for key,value in options.items() if key!='github_http'}
            service,_=build_control(root,'x'*40,**token_options,github_token='fixture')
            try:self.assertIsInstance(service.task_intake.intake.client,CodexAppsPlan)
            finally:service.lifecycle.close();service.close()

    def test_gateway_rejects_token_file_before_reading_it(self):
        """Reject a tracker token file before any secret is read in gateway mode."""
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

    def test_pr_actions_are_unavailable_without_trusted_authority(self):
        from runner.control import ControlError
        with tempfile.TemporaryDirectory() as root:
            service,control=build_control(root,'x'*40)
            try:
                with self.assertRaises(ControlError): control.handle('x'*40,{'action':'pr_propose','run':'r','queue_claim':'q','graph_claim':'g','kind':'push','parameters':{'expected_remote':None}})
            finally: service.close()

    def test_runtime_config_file_is_private_json(self):
        import json
        from runner.worker import read_runtime_config
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'runtime.json'; path.write_text(json.dumps({'socket':'/fake/socket'})); path.chmod(0o600)
            self.assertEqual(read_runtime_config(path),{'socket':'/fake/socket'})
            path.chmod(0o644)
            with self.assertRaises(ConfigurationError): read_runtime_config(path)

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

    def test_manual_stop_reports_incomplete_scope_schema_without_unfencing(self):
        from runner.manual_lifecycle import ManualLifecycle
        from runner.control import ControlError
        with tempfile.TemporaryDirectory() as root:
            service,control=build_control(root,'x'*40,lifecycle_factory=lambda _:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.create('run','marketing','task');service.enqueue('run');service.lifecycle.claim()
                service.graph.db.execute('CREATE TABLE local_scopes(run TEXT,state TEXT)')
                service.graph.db.execute("INSERT INTO local_scopes VALUES('run','starting')")
                result=control.handle('x'*40,{'action':'manual_stop','run':'run'})
                self.assertIs(result['local_scopes_unavailable'],True)
                with self.assertRaises(ControlError):control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run','incarnation':'inc','attestation':'terminated_and_fenced'})
                service.graph.db.execute("UPDATE local_scopes SET state='terminated' WHERE run='run'")
                with self.assertRaises(ControlError):control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run','incarnation':'inc','attestation':'terminated_and_fenced'})
                self.assertEqual(service.queue.get('run')['status'],'cancelling')
            finally:service.lifecycle.close();service.close()

    def test_null_scope_state_remains_visible_and_blocks_confirmation(self):
        from runner.manual_lifecycle import ManualLifecycle
        from runner.control import ControlError
        with tempfile.TemporaryDirectory() as root:
            service,control=build_control(root,'x'*40,lifecycle_factory=lambda _:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.create('run','marketing','task');service.enqueue('run');service.lifecycle.claim()
                service.graph.db.execute('CREATE TABLE local_scopes(run TEXT,unit TEXT,invocation TEXT,cgroup TEXT,state TEXT)')
                service.graph.db.execute("INSERT INTO local_scopes VALUES('run','unknown.scope',NULL,NULL,NULL)")
                result=control.handle('x'*40,{'action':'manual_stop','run':'run'})
                self.assertEqual(result['local_scopes'],[{'unit':'unknown.scope','state':None}])
                with self.assertRaises(ControlError):control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run','incarnation':'inc','attestation':'terminated_and_fenced'})
                self.assertEqual(service.queue.get('run')['status'],'cancelling')
            finally:service.lifecycle.close();service.close()

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

    def test_startup_requires_seo_skill_for_canonical_draft_and_review(self):
        with tempfile.TemporaryDirectory() as root:
            skills=Path(root)/'skills'; skills.mkdir()
            config={'skill_roots':[str(skills)],'required_skills':{'canonical_draft':['writer'],'canonical_review':['editor']}}
            service,control=build_control(root,'x'*40,role_config=config)
            try:
                self.assertEqual(service.required_skills['canonical_draft'],['writer','plan-seo-geo-growth'])
                self.assertEqual(service.required_skills['canonical_review'],['editor','plan-seo-geo-growth'])
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
