import tempfile
import unittest
from runner.control import ControlPlane, ControlError
from runner.service import Service

class ControlTests(unittest.TestCase):
    def test_metrics_are_authenticated_and_failure_does_not_change_workflow(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); control=ControlPlane(service,'synthetic-secret')
            try:
                service.create('private-run','engineering','private-task'); service.enqueue('private-run')
                with self.assertRaises(ControlError): control.handle('wrong',{'action':'metrics'})
                result=control.handle('synthetic-secret',{'action':'metrics'})['metrics']
                self.assertIn('workflow_jobs{status="queued"} 1',result)
                self.assertNotIn('private',result)
                with patch('runner.metrics.snapshot',side_effect=RuntimeError('private failure')):
                    with self.assertRaisesRegex(ControlError,'request failed'): control.handle('synthetic-secret',{'action':'metrics'})
                self.assertEqual(service.queue.get('private-run')['status'],'queued')
            finally: service.close()

    def test_authentication_and_default_no_evidence_injection(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); control=ControlPlane(service,'synthetic-secret')
            with self.assertRaises(ControlError): control.handle('wrong',{'action':'create','run':'r','workflow':'marketing','task':'t'})
            control.handle('synthetic-secret',{'action':'create','run':'r','workflow':'marketing','task':'t'})
            token=control.handle('synthetic-secret',{'action':'claim','run':'r'})['claim']
            with self.assertRaises(ControlError): control.handle('synthetic-secret',{'action':'complete','run':'r','stage':'intake','claim':token,'evidence':{}})
            control.handle('synthetic-secret',{'action':'cancel','run':'r','claim':token})
            service.close()

    def test_unix_socket_rejects_wrong_secret(self):
        import json, socket, threading
        from pathlib import Path
        from runner.control import UnixControlServer
        class Dummy:
            def create(self,*args): raise AssertionError('unauthorized request reached service')
        with tempfile.TemporaryDirectory() as root:
            try: server=UnixControlServer(Path(root)/'control.sock',ControlPlane(Dummy(),'synthetic-secret'))
            except PermissionError: self.skipTest('sandbox denies Unix socket bind')
            thread=threading.Thread(target=server.handle_request); thread.start()
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.connect(str(Path(root)/'control.sock'))
                client.sendall(json.dumps({'secret':'wrong','request':{'action':'create'}}).encode()+b'\n')
                self.assertFalse(json.loads(client.recv(4096))['ok'])
            thread.join(2); server.server_close()

    def test_authenticated_review_artifact_is_accessible(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); control=ControlPlane(service,'synthetic-secret')
            ref=service.artifacts.put(json.dumps({'review':'synthetic'}).encode())
            self.assertEqual(control.handle('synthetic-secret',{'action':'artifact','reference':ref})['artifact']['review'],'synthetic')
            service.close()

    def test_cli_reads_large_authenticated_artifact_over_socket(self):
        import json, os, threading
        from pathlib import Path
        from unittest.mock import patch
        from runner.cli import request_control
        from runner.control import UnixControlServer
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); secret=Path(root)/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            content='\x7f'*(1048576-20)
            ref=service.artifacts.put(json.dumps({'review':content},ensure_ascii=False).encode())
            server=UnixControlServer(Path(root)/'control.sock',ControlPlane(service,'x'*40))
            thread=threading.Thread(target=server.handle_request); thread.start()
            try:
                with patch.dict(os.environ,{'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_DATA_DIR':root}):
                    result=request_control({'action':'artifact','reference':ref})
                self.assertEqual(result['result']['artifact']['review'],content)
            finally: thread.join(5); server.server_close(); service.close()

    def test_cancel_job_fences_graph_writes(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); control=ControlPlane(service,'synthetic-secret')
            service.create('r','marketing','t'); service.enqueue('r'); service.queue.claim('worker',60); claim=service.claim('r')
            control.handle('synthetic-secret',{'action':'cancel_job','run':'r'})
            self.assertEqual(service.queue.get('r')['status'],'cancelling')
            with self.assertRaises(Exception): service.graph.prepare('r','intake',claim)
            service.close()

    def test_manual_lifecycle_remains_available_in_auto_mode(self):
        class LifecycleFixture:
            auto=True
            def manual_stop(self,run): return {'incarnation':'inc-1','service':'svc','application':'app','evidence_required':'proof'}
        with tempfile.TemporaryDirectory() as root:
            service=Service(root,lifecycle=LifecycleFixture()); control=ControlPlane(service,'synthetic-secret')
            service.create('r','marketing','task')
            result=control.handle('synthetic-secret',{'action':'manual_stop','run':'r'})
            self.assertEqual(result['incarnation'],'inc-1'); service.close()

    def test_verified_manual_stop_fences_work_and_frees_queue(self):
        from runner.lifecycle import Lifecycle
        class Observer:
            current='inc-1'
            def current_incarnation(self,target): return self.current
            def verify_stopped_and_fenced(self,target,incarnation,evidence): return self.current is None and evidence=='independent-proof'
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); observer=Observer()
            policy={'origin':'https://coolify.example.invalid','service':'svc','application':'app','controller_id':'c','permission_scope':'manual','verifier_id':'v'}
            lifecycle=Lifecycle(root+'/state.db',policy,observer); service.lifecycle=lifecycle
            service.create('r','marketing','task'); service.enqueue('r'); service.queue.claim('worker',60); claim=service.claim('r'); lifecycle.register('r','inc-1')
            control=ControlPlane(service,'synthetic-secret'); notice=control.handle('synthetic-secret',{'action':'manual_stop','run':'r'})
            self.assertEqual(notice['incarnation'],'inc-1')
            with self.assertRaises(Exception): service.graph.prepare('r','intake',claim)
            self.assertEqual(service.queue.get('r')['status'],'cancelling')
            observer.current=None
            control.handle('synthetic-secret',{'action':'confirm_stopped','run':'r','evidence':'independent-proof'})
            self.assertEqual(service.queue.get('r')['status'],'cancelled')
            lifecycle.close(); service.close()

    def test_automatic_mode_requires_exact_approval_and_trusted_test(self):
        from runner.lifecycle import Lifecycle
        class Observer:
            def current_incarnation(self,target): return 'inc-1'
        class Controller: pass
        with tempfile.TemporaryDirectory() as root:
            policy={'origin':'https://coolify.example.invalid','service':'svc','application':'app','controller_id':'c','permission_scope':'dedicated-team-deploy','verifier_id':'v'}
            service=Service(root); lifecycle=Lifecycle(root+'/state.db',policy,Observer(),Controller()); service.lifecycle=lifecycle
            service.lifecycle_policy_verifier=lambda policy_hash: policy_hash==lifecycle.policy_hash
            control=ControlPlane(service,'synthetic-secret')
            with self.assertRaises(Exception): control.handle('synthetic-secret',{'action':'enable_lifecycle_auto'})
            control.handle('synthetic-secret',{'action':'approve','run':'controller','approval':'enable_lifecycle','version':lifecycle.policy_hash})
            control.handle('synthetic-secret',{'action':'enable_lifecycle_auto'})
            self.assertTrue(lifecycle.auto); lifecycle.close(); service.close()

    def test_stop_confirmation_recovers_after_queue_update_interruption(self):
        from runner.lifecycle import Lifecycle
        class Observer:
            current='inc-1'
            def current_incarnation(self,target): return self.current
            def verify_stopped_and_fenced(self,target,incarnation,evidence): return self.current is None and evidence=='proof'
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); observer=Observer()
            policy={'origin':'https://coolify.example.invalid','service':'svc','application':'app','controller_id':'c','permission_scope':'manual','verifier_id':'v'}
            lifecycle=Lifecycle(root+'/state.db',policy,observer); service.lifecycle=lifecycle
            service.create('r','marketing','task'); service.enqueue('r'); service.queue.claim('worker',60); lifecycle.register('r','inc-1')
            service.request_lifecycle_stop('r'); observer.current=None
            lifecycle.confirm_stopped('r','proof')  # simulate crash before queue reconciliation
            service.confirm_lifecycle_stop('r','proof')
            self.assertEqual(service.queue.get('r')['status'],'cancelled'); lifecycle.close(); service.close()

    def test_automatic_mode_restores_only_after_approval_and_reverification(self):
        from runner.lifecycle import Lifecycle
        class Observer:
            def current_incarnation(self,target): return 'inc-1'
        class Controller: pass
        with tempfile.TemporaryDirectory() as root:
            policy={'origin':'https://coolify.example.invalid','service':'svc','application':'app','controller_id':'c','permission_scope':'dedicated-team-deploy','verifier_id':'v'}
            service=Service(root); lifecycle=Lifecycle(root+'/state.db',policy,Observer(),Controller()); service.lifecycle=lifecycle; service.lifecycle_policy_verifier=lambda h:True
            service.approve('controller','enable_lifecycle',lifecycle.policy_hash,'owner'); service.enable_lifecycle_auto()
            lifecycle.close(); service.close()
            restored_lifecycle=Lifecycle(root+'/state.db',policy,Observer(),Controller())
            restored=Service(root,lifecycle=restored_lifecycle,lifecycle_policy_verifier=lambda h:True)
            self.assertFalse(restored_lifecycle.auto)
            restored.restore_lifecycle_auto(); self.assertTrue(restored_lifecycle.auto)
            restored.disable_lifecycle_auto()
            with self.assertRaises(Exception): restored.restore_lifecycle_auto()
            restored_lifecycle.close(); restored.close()

    def test_automatic_mode_can_be_reapproved_after_disable(self):
        from runner.lifecycle import Lifecycle
        class Observer: pass
        class Controller: pass
        with tempfile.TemporaryDirectory() as root:
            policy={'origin':'https://coolify.example.invalid','service':'svc','application':'app','controller_id':'c','permission_scope':'dedicated-team-deploy','verifier_id':'v'}
            service=Service(root); lifecycle=Lifecycle(root+'/state.db',policy,Observer(),Controller()); service.lifecycle=lifecycle; service.lifecycle_policy_verifier=lambda h:True
            service.approve('controller','enable_lifecycle',lifecycle.policy_hash,'owner'); service.enable_lifecycle_auto(); service.disable_lifecycle_auto()
            service.approve('controller','enable_lifecycle',lifecycle.policy_hash,'owner'); service.enable_lifecycle_auto()
            self.assertTrue(lifecycle.auto); lifecycle.close(); service.close()
