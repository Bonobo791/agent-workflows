import tempfile
import unittest
from runner.lifecycle import Lifecycle, LifecycleError

class Observer:
    def __init__(self): self.current='inc-1'; self.stopped=False
    def current_incarnation(self,target): return self.current
    def verify_stopped_and_fenced(self,target,incarnation,evidence): return self.stopped and self.current is None and evidence=='proof'
    def run_if_current(self,target,incarnation,operation):
        if self.current!=incarnation: raise RuntimeError('changed')
        return operation(target)
    def run_if_stopped(self,target,incarnation,operation):
        if self.current is not None: raise RuntimeError('changed')
        return operation(target)
    def verify_operation_quiesced(self,target,run,evidence): return self.current is None and evidence=='proof'
    def verify_restarted(self,target,old,evidence): return self.current if self.current and self.current!=old and evidence=='restart-proof' else None
class Controller:
    def __init__(self): self.stops=[]; self.restarts=[]; self.fail=False
    def stop(self,target):
        self.stops.append(target)
        if self.fail: raise TimeoutError()
    def restart(self,target): self.restarts.append(target)

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.observer=Observer(); self.controller=Controller()
        self.l=Lifecycle(self.tmp.name+'/db',{'origin':'https://coolify.example.invalid','service':'svc','application':'app','controller_id':'controller-v1','permission_scope':'dedicated-team-deploy','verifier_id':'observer-v1'},self.observer,self.controller)
        self.l.register('run','inc-1')
    def tearDown(self): self.l.close(); self.tmp.cleanup()
    def test_manual_always_available_and_requires_independent_confirmation(self):
        notice=self.l.manual_stop('run'); self.assertEqual(notice['incarnation'],'inc-1')
        with self.assertRaises(LifecycleError): self.l.confirm_stopped('run','proof')
        self.observer.current=None; self.observer.stopped=True
        self.l.confirm_stopped('run','proof'); self.assertEqual(self.l.status('run')['status'],'stopped')
    def test_auto_is_disabled_until_exact_policy_approved_and_verified(self):
        with self.assertRaises(LifecycleError): self.l.automatic_stop('run')
        self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value: value==self.l.policy_hash)
        self.l.automatic_stop('run'); self.l.automatic_stop('run')
        self.assertEqual(len(self.controller.stops),1)
        self.assertEqual(self.l.status('run')['status'],'requested')
    def test_timeout_is_uncertain_and_manual_override_remains(self):
        self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value:True); self.controller.fail=True
        with self.assertRaises(LifecycleError): self.l.automatic_stop('run')
        self.assertEqual(self.l.status('run')['status'],'uncertain')
        self.assertEqual(self.l.manual_stop('run')['incarnation'],'inc-1')
        with self.assertRaises(LifecycleError): self.l.automatic_stop('run')
        self.assertEqual(len(self.controller.stops),1)
    def test_stale_incarnation_never_stops_new_worker(self):
        self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value:True); self.observer.current='inc-2'
        with self.assertRaises(LifecycleError): self.l.automatic_stop('run')
        self.assertEqual(self.controller.stops,[])

    def test_restart_requires_stopped_worker_and_new_verified_incarnation(self):
        self.l.manual_stop('run'); self.observer.current=None; self.observer.stopped=True; self.l.confirm_stopped('run','proof')
        self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value:True)
        self.l.automatic_restart('run'); self.assertEqual(len(self.controller.restarts),1)
        with self.assertRaises(LifecycleError): self.l.confirm_restarted('run','bad')
        self.observer.current='inc-2'
        self.assertEqual(self.l.confirm_restarted('run','restart-proof'),'inc-2')
        self.assertEqual(self.l.status('run')['status'],'restarted')
    def test_restart_timeout_does_not_repeat_and_allows_manual_guidance(self):
        self.l.manual_stop('run'); self.observer.current=None; self.observer.stopped=True; self.l.confirm_stopped('run','proof')
        self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value:True)
        def timeout(target): self.controller.restarts.append(target); raise TimeoutError()
        self.controller.restart=timeout
        with self.assertRaises(LifecycleError): self.l.automatic_restart('run')
        with self.assertRaises(LifecycleError): self.l.automatic_restart('run')
        self.assertEqual(len(self.controller.restarts),1)
        self.assertEqual(self.l.manual_restart('run')['incarnation'],'inc-1')

    def test_one_worker_incarnation_cannot_be_registered_to_two_runs(self):
        with self.assertRaises(Exception): self.l.register('other','inc-1')

    def test_hung_controller_is_bounded_and_uncertain(self):
        import threading
        self.l.timeout=0.02; self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value:True)
        wait=threading.Event()
        def hang(target): wait.wait(1)
        self.controller.stop=hang
        try:
            with self.assertRaises(LifecycleError): self.l.automatic_stop('run')
            self.assertEqual(self.l.status('run')['status'],'uncertain')
            with self.assertRaises(LifecycleError): self.l.automatic_stop('run')
        finally: wait.set()

    def test_same_physical_incarnation_cannot_use_another_policy(self):
        target={**self.l.target,'controller_id':'different-controller'}
        other=Lifecycle(self.tmp.name+'/db',target,self.observer,self.controller)
        try:
            with self.assertRaises(Exception): other.register('other-run','inc-1')
        finally: other.close()

    def test_guard_refusal_does_not_claim_request_was_issued(self):
        self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value:True)
        self.observer.run_if_current=lambda *args:False
        with self.assertRaises(LifecycleError): self.l.automatic_stop('run')
        self.assertEqual(self.l.status('run')['status'],'uncertain')
        self.assertEqual(self.controller.stops,[])

    def test_automatic_mode_requires_reverification_after_restart(self):
        self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value:True)
        other=Lifecycle(self.tmp.name+'/db',self.l.target,self.observer,self.controller)
        try:
            self.assertFalse(other.auto)
            with self.assertRaises(LifecycleError): other.restore_auto(lambda value:False)
            self.assertFalse(other.auto)
            other.restore_auto(lambda value:True); self.assertTrue(other.auto)
        finally: other.close()

    def test_hung_stop_cannot_be_confirmed_while_call_still_running(self):
        import threading
        self.l.timeout=0.02; self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value:True)
        blocked=threading.Event()
        self.controller.stop=lambda target: blocked.wait(1)
        try:
            with self.assertRaises(LifecycleError): self.l.automatic_stop('run')
            self.observer.current=None; self.observer.stopped=True
            with self.assertRaises(LifecycleError): self.l.confirm_stopped('run','proof')
            blocked.set(); self.assertTrue(self.l.inflight['run'].wait(1))
            self.l.confirm_stopped('run','proof')
        finally: blocked.set()
    def test_stop_confirmation_is_idempotent_after_reverification(self):
        self.l.manual_stop('run'); self.observer.current=None; self.observer.stopped=True
        self.l.confirm_stopped('run','proof'); self.l.confirm_stopped('run','proof')

    def test_second_instance_cannot_release_target_during_inflight_call(self):
        import threading
        self.l.timeout=0.02; self.l.enable_auto(approved_policy=self.l.policy_hash,verify_policy=lambda value:True)
        blocked=threading.Event(); self.controller.stop=lambda target: blocked.wait(1)
        try:
            with self.assertRaises(LifecycleError): self.l.automatic_stop('run')
            self.observer.current=None; self.observer.stopped=True
            other=Lifecycle(self.tmp.name+'/db',self.l.target,self.observer,self.controller)
            try:
                with self.assertRaises(LifecycleError): other.confirm_stopped('run','proof')
            finally: other.close()
        finally:
            blocked.set()
            self.l.inflight['run'].wait(1)

    def test_crash_left_active_call_requires_external_quiescence_evidence(self):
        self.l.manual_stop('run')
        self.l.db.execute("INSERT INTO lifecycle_calls VALUES(?,?,?,1)",(self.l.target_hash,'run','lost-call'))
        self.l.db.execute("UPDATE worker_lifecycle SET status='uncertain' WHERE run='run'")
        with self.assertRaises(LifecycleError): self.l.resolve_interrupted_call('run','bad')
        self.observer.current=None; self.observer.stopped=True
        self.l.resolve_interrupted_call('run','proof')
        self.l.confirm_stopped('run','proof')
        self.assertEqual(self.l.status('run')['status'],'stopped')

    def test_manual_stop_refuses_stale_replacement(self):
        self.observer.current='inc-2'
        with self.assertRaises(LifecycleError): self.l.manual_stop('run')
        self.assertFalse(self.l.status('run')['manual_requested'])
