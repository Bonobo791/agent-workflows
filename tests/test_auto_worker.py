import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from runner.auto_worker import AutoWorker, AutoWorkerError, renew_leases
from runner.manual_lifecycle import ManualLifecycle
from runner.service import Service

class AutoWorkerTests(unittest.TestCase):
    def test_idle_checkpoint_allows_fresh_worker_claim_without_old_tokens(self):
        from types import SimpleNamespace
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);claims=[];incarnation=['inc']
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker',incarnation[0])
                scopes=LocalScopes(str(root/'state.db'),Backend());scopes.close()
                service.graph.db.execute('CREATE TABLE IF NOT EXISTS local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                service.model_supervisor=SimpleNamespace(db=str(root/'state.db'))
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,run,queue,claim):claims.append((queue,claim));return {'stage':'freeze','status':'awaiting_approval'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            first=AutoWorker(factory,root);second=None
            try:
                first.start();deadline=time.monotonic()+5
                while not first.status().get('checkpoint_available') and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(first.status()['status'],'awaiting_approval')
                self.assertTrue(first.status()['checkpoint_available'])
                self.assertTrue(first.request_checkpoint('r')['pending'])
                first.thread.join(timeout=5);self.assertFalse(first.thread.is_alive())
                self.assertEqual(first.status()['status'],'checkpointed')
                self.assertEqual(first.status()['last_checkpoint'],{'status':'completed'})
                owner=ManualLifecycle(str(root/'state.db'),'worker','inc')
                try:owner.register_checkpoint_replacement('r','inc','inc-next')
                finally:owner.close()
                incarnation[0]='inc-next'
                second=AutoWorker(factory,root);second.start();deadline=time.monotonic()+5
                while len(claims)<2 and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(len(claims),2);self.assertNotEqual(claims[0][0],claims[1][0]);self.assertNotEqual(claims[0][1],claims[1][1])
                db=sqlite3.connect(root/'state.db')
                try:self.assertEqual(db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('active',))
                finally:db.close()
            finally:
                first.stop()
                if first.thread:first.thread.join(timeout=5)
                if second:
                    second.stop()
                    if second.thread:second.thread.join(timeout=5)

    def test_inflight_heartbeat_after_checkpoint_does_not_fence_requeued_run(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);entered=threading.Event();release=threading.Event();calls=[]
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                scopes=LocalScopes(str(root/'state.db'),Backend());scopes.close()
                service.graph.db.execute('CREATE TABLE IF NOT EXISTS local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                service.model_supervisor=SimpleNamespace(db=str(root/'state.db'))
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):return {'status':'blocked'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            def heartbeat(*args,**kwargs):
                calls.append(1)
                if len(calls)>1:entered.set();release.wait(5)
                return renew_leases(*args,**kwargs)
            worker=AutoWorker(factory,root)
            try:
                with patch('runner.auto_worker.renew_leases',side_effect=heartbeat):
                    worker.start();self.assertTrue(entered.wait(15))
                    self.assertTrue(worker.status()['checkpoint_available']);worker.request_checkpoint('r')
                    deadline=time.monotonic()+5
                    while worker.status()['status']!='checkpointed' and time.monotonic()<deadline:time.sleep(.01)
                    self.assertEqual(worker.status()['status'],'checkpointed');release.set();worker.thread.join(timeout=5)
                    self.assertFalse(worker.thread.is_alive())
                with sqlite3.connect(root/'state.db') as db:
                    self.assertEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('queued',))
                    self.assertEqual(db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('active',))
            finally:
                release.set();worker.stop()
                if worker.thread:worker.thread.join(timeout=5)

    def test_slow_rejected_checkpoint_does_not_trip_heartbeat_fence(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        from runner.checkpoint import CheckpointError
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);locked=threading.Event();heartbeat=threading.Event();busy=threading.Event();release=threading.Event();calls=[]
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                scopes=LocalScopes(str(root/'state.db'),Backend());scopes.close()
                service.graph.db.execute('CREATE TABLE IF NOT EXISTS local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                service.model_supervisor=SimpleNamespace(db=str(root/'state.db'))
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):return {'status':'blocked'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            def slow_check(*args):locked.set();release.wait(9);raise CheckpointError('unresolved')
            def renewal(*args,**kwargs):
                calls.append(1)
                if len(calls)>1:
                    locked.wait(3);heartbeat.set()
                try:return renew_leases(*args,**kwargs)
                except sqlite3.OperationalError:busy.set();raise
            worker=AutoWorker(factory,root)
            try:
                with patch('runner.checkpoint.ensure_quiescent',side_effect=slow_check),patch('runner.auto_worker.renew_leases',side_effect=renewal),patch('runner.auto_worker.HEARTBEAT_INTERVAL',.02):
                    worker.start();deadline=time.monotonic()+5
                    while not worker.status().get('checkpoint_available') and time.monotonic()<deadline:time.sleep(.01)
                    self.assertTrue(worker.status()['checkpoint_available']);worker.request_checkpoint('r')
                    self.assertTrue(locked.wait(3));self.assertTrue(heartbeat.wait(3));self.assertTrue(busy.wait(7));release.set()
                    deadline=time.monotonic()+5
                    while (worker.status().get('last_checkpoint') or {}).get('status')!='rejected' and time.monotonic()<deadline:time.sleep(.01)
                    worker.wake.set();worker.thread.join(timeout=2)
                    self.assertTrue(worker.thread.is_alive(),'heartbeat contention fenced the running task')
                    self.assertNotEqual(worker.status()['status'],'inspection_required')
                    with sqlite3.connect(root/'state.db') as db:self.assertEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('running',))
            finally:
                release.set();worker.stop()
                if worker.thread:worker.thread.join(timeout=5)

    def test_busy_heartbeat_returning_after_rejected_checkpoint_rechecks_claim(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        from runner.checkpoint import CheckpointError
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);locked=threading.Event();heartbeat=threading.Event();release_checkpoint=threading.Event();release_heartbeat=threading.Event();calls=[]
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                scopes=LocalScopes(str(root/'state.db'),Backend());scopes.close()
                service.graph.db.execute('CREATE TABLE IF NOT EXISTS local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                service.model_supervisor=SimpleNamespace(db=str(root/'state.db'))
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):return {'status':'blocked'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            def slow_check(*args):locked.set();release_checkpoint.wait(5);raise CheckpointError('unresolved')
            def renewal(*args,**kwargs):
                calls.append(1)
                if len(calls)==2:
                    locked.wait(3);heartbeat.set();release_heartbeat.wait(5)
                    error=sqlite3.OperationalError('database is locked');error.sqlite_errorcode=sqlite3.SQLITE_BUSY;raise error
                return renew_leases(*args,**kwargs)
            worker=AutoWorker(factory,root)
            try:
                with patch('runner.checkpoint.ensure_quiescent',side_effect=slow_check),patch('runner.auto_worker.renew_leases',side_effect=renewal),patch('runner.auto_worker.HEARTBEAT_INTERVAL',.02):
                    worker.start();deadline=time.monotonic()+5
                    while not worker.status().get('checkpoint_available') and time.monotonic()<deadline:time.sleep(.01)
                    self.assertTrue(worker.status()['checkpoint_available']);worker.request_checkpoint('r')
                    self.assertTrue(locked.wait(3));self.assertTrue(heartbeat.wait(3));release_checkpoint.set()
                    deadline=time.monotonic()+3
                    while (worker.status().get('last_checkpoint') or {}).get('status')!='rejected' and time.monotonic()<deadline:time.sleep(.01)
                    self.assertEqual(worker.status()['last_checkpoint']['status'],'rejected')
                    release_heartbeat.set();time.sleep(.2);worker.wake.set();worker.thread.join(timeout=2)
                    self.assertTrue(worker.thread.is_alive(),'late busy response fenced the running task')
                    with sqlite3.connect(root/'state.db') as db:self.assertEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('running',))
            finally:
                release_checkpoint.set();release_heartbeat.set();worker.stop()
                if worker.thread:worker.thread.join(timeout=5)

    def test_heartbeat_recheck_fences_expired_claim_after_checkpoint_contention(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        from runner.checkpoint import CheckpointError
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);locked=threading.Event();busy=threading.Event();release=threading.Event();calls=[]
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                scopes=LocalScopes(str(root/'state.db'),Backend());scopes.close()
                service.graph.db.execute('CREATE TABLE IF NOT EXISTS local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                service.model_supervisor=SimpleNamespace(db=str(root/'state.db'))
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):return {'status':'blocked'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            def slow_check(*args):locked.set();release.wait(5);raise CheckpointError('unresolved')
            def renewal(*args,**kwargs):
                calls.append(1)
                if len(calls)==1:return renew_leases(*args,**kwargs)
                if len(calls)==2:
                    locked.wait(3);error=sqlite3.OperationalError('database is locked');error.sqlite_errorcode=sqlite3.SQLITE_BUSY;busy.set();raise error
                raise AutoWorkerError('lease expired')
            worker=AutoWorker(factory,root)
            try:
                with patch('runner.checkpoint.ensure_quiescent',side_effect=slow_check),patch('runner.auto_worker.renew_leases',side_effect=renewal),patch('runner.auto_worker.HEARTBEAT_INTERVAL',.02):
                    worker.start();deadline=time.monotonic()+5
                    while not worker.status().get('checkpoint_available') and time.monotonic()<deadline:time.sleep(.01)
                    self.assertTrue(worker.status()['checkpoint_available']);worker.request_checkpoint('r')
                    self.assertTrue(locked.wait(3));self.assertTrue(busy.wait(3));release.set();worker.thread.join(timeout=5)
                    self.assertFalse(worker.thread.is_alive());self.assertEqual(worker.status()['status'],'inspection_required')
                    with sqlite3.connect(root/'state.db') as db:self.assertEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('cancelling',))
            finally:
                release.set();worker.stop()
                if worker.thread:worker.thread.join(timeout=5)

    def test_stop_during_checkpoint_does_not_fence_requeued_run(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        from runner.checkpoint import checkpoint
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);entered=threading.Event();release=threading.Event()
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                scopes=LocalScopes(str(root/'state.db'),Backend());scopes.close()
                service.graph.db.execute('CREATE TABLE IF NOT EXISTS local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                service.model_supervisor=SimpleNamespace(db=str(root/'state.db'))
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):return {'status':'awaiting_approval'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            controller=Service(root);controller.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
            worker=AutoWorker(factory,root)
            def delayed(*args):entered.set();release.wait(5);return checkpoint(*args)
            try:
                with patch('runner.checkpoint.checkpoint',side_effect=delayed):
                    worker.start();deadline=time.monotonic()+5
                    while not worker.status().get('checkpoint_available') and time.monotonic()<deadline:time.sleep(.01)
                    self.assertTrue(worker.status()['checkpoint_available']);worker.request_checkpoint('r')
                    self.assertTrue(entered.wait(5));timer=threading.Timer(.1,release.set);timer.start()
                    worker.stop(controller);timer.join();worker.thread.join(timeout=5)
                self.assertFalse(worker.thread.is_alive());self.assertEqual(worker.status()['status'],'checkpointed')
                self.assertEqual(controller.graph.db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('active',))
                self.assertEqual(controller.graph.db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('queued',))
            finally:
                release.set()
                if worker.thread:worker.thread.join(timeout=5)
                controller.lifecycle.close();controller.close()

    def test_accepted_owner_action_wakes_waiting_worker(self):
        import threading
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);processed=threading.Event()
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):return {'status':'blocked'}
                class Control:
                    secret='fixture'
                    def handle(self,*args):processed.set();return {'ok':True}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,Control()
            worker=AutoWorker(factory,root)
            try:
                worker.start();deadline=time.monotonic()+5
                while worker.status()['status']!='blocked' and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(worker.status()['status'],'blocked')
                worker.request_action('r','pr_withdraw',{'proposal':'fixture'})
                self.assertTrue(processed.wait(2),'accepted action did not wake the worker')
            finally:
                worker.stop()
                if worker.thread:worker.thread.join(timeout=5)

    def test_checkpoint_rejected_while_dispatch_is_running(self):
        import threading
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);entered=threading.Event();release=threading.Event()
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):entered.set();release.wait(5);return {'status':'blocked'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            worker=AutoWorker(factory,root)
            try:
                worker.start();self.assertTrue(entered.wait(5))
                with self.assertRaises(AutoWorkerError):worker.request_checkpoint('r')
            finally:
                release.set();worker.stop()
                if worker.thread:worker.thread.join(timeout=5)

    def test_unresolved_scope_rejects_checkpoint_without_releasing_claims(self):
        from types import SimpleNamespace
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);claims=[]
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                scopes=LocalScopes(str(root/'state.db'),Backend());scopes.close()
                service.graph.db.execute('CREATE TABLE IF NOT EXISTS local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                service.model_supervisor=SimpleNamespace(db=str(root/'state.db'))
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,run,queue,claim):claims.append((queue,claim));return {'status':'blocked'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            worker=AutoWorker(factory,root)
            try:
                worker.start();deadline=time.monotonic()+5
                while not worker.status().get('checkpoint_available') and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(worker.status()['checkpoint_available'])
                scopes=LocalScopes(str(root/'state.db'),Backend())
                try:scopes.reserve('r')
                finally:scopes.close()
                worker.request_checkpoint('r');deadline=time.monotonic()+5
                while len(claims)<2 and time.monotonic()<deadline:time.sleep(.01)
                self.assertGreaterEqual(len(claims),2);self.assertEqual(claims[0],claims[1])
                self.assertEqual(worker.status()['last_checkpoint'],{'status':'rejected','reason':'work remains active or unresolved'})
                db=sqlite3.connect(root/'state.db')
                try:self.assertEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('running',))
                finally:db.close()
            finally:
                worker.stop()
                if worker.thread:worker.thread.join(timeout=5)

    def test_checkpoint_storage_failure_requires_inspection(self):
        from types import SimpleNamespace
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                scopes=LocalScopes(str(root/'state.db'),Backend());scopes.close()
                db=service.graph.db
                db.execute('CREATE TABLE IF NOT EXISTS local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS checkpoint_audit(id INTEGER PRIMARY KEY,schema_version INTEGER NOT NULL,run TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
                db.execute("CREATE TRIGGER reject_checkpoint BEFORE INSERT ON checkpoint_audit BEGIN SELECT RAISE(ABORT,'unavailable'); END")
                service.model_supervisor=SimpleNamespace(db=str(root/'state.db'))
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):return {'status':'blocked'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            worker=AutoWorker(factory,root)
            try:
                worker.start();deadline=time.monotonic()+5
                while not worker.status().get('checkpoint_available') and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(worker.status()['checkpoint_available']);worker.request_checkpoint('r')
                worker.thread.join(timeout=5)
                self.assertFalse(worker.thread.is_alive())
                self.assertEqual(worker.status()['status'],'inspection_required')
                self.assertEqual(worker.status()['last_checkpoint'],{'status':'inspection_required'})
                with sqlite3.connect(root/'state.db') as db:
                    self.assertNotEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('queued',))
            finally:
                worker.stop()
                if worker.thread:worker.thread.join(timeout=5)

    def test_checkpoint_cannot_race_pending_owner_actions_or_stop(self):
        with tempfile.TemporaryDirectory() as root:
            worker=AutoWorker(lambda:None,Path(root));worker.checkpoint_capable=True;worker.at_gate=True
            worker._set(status='awaiting_approval',run='r')
            worker.request_action('r','pr_propose',{'kind':'push','parameters':{'expected_remote':None}})
            with self.assertRaises(AutoWorkerError):worker.request_checkpoint('r')
            worker.pending_action=None
            self.assertTrue(worker.request_checkpoint('r')['pending'])
            with self.assertRaises(AutoWorkerError):worker.request_action('r','pr_propose',{'kind':'push','parameters':{'expected_remote':None}})
            worker.stop();self.assertFalse(worker.status()['checkpoint_available'])
            with self.assertRaises(AutoWorkerError):worker.request_checkpoint('r')
            stopped=AutoWorker(lambda:None,Path(root));stopped.checkpoint_capable=True;stopped.at_gate=True
            stopped._set(status='awaiting_approval',run='r');stopped.stop()
            self.assertFalse(stopped.status()['checkpoint_available'])

    def test_stop_does_not_cancel_completion_already_in_progress(self):
        import threading
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); entered=threading.Event(); release=threading.Event()
            def factory():
                service=Service(root); service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self): service.create('r','marketing','task'); service.enqueue('r')
                class Dispatcher:
                    def step(self,*args): return {'status':'awaiting_review'}
                class Human:
                    def current(self,run): return {'decision':'approved'}
                def finish(run,token):
                    entered.set(); release.wait(5); service.queue.finish(run,token,verify_completed=lambda _:True)
                service.task_intake=Intake(); service.stage_dispatcher=Dispatcher(); service.human_review=Human(); service.finish_job=finish
                return service,None
            controller=Service(root); controller.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
            worker=AutoWorker(factory,root)
            try:
                worker.start(); self.assertTrue(entered.wait(5))
                timer=threading.Timer(.1,release.set); timer.start()
                worker.stop(controller); timer.join(); worker.thread.join(timeout=5)
                self.assertEqual(controller.graph.db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('completed',))
                self.assertEqual(worker.status()['status'],'completed')
            finally:
                release.set()
                if worker.thread: worker.thread.join(timeout=5)
                controller.lifecycle.close(); controller.close()

    def test_stop_before_claim_prevents_late_assignment(self):
        import threading
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); entered=threading.Event(); release=threading.Event()
            def factory():
                service=Service(root); service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):
                        service.create('r','engineering','task'); service.enqueue('r'); entered.set(); release.wait(5)
                service.task_intake=Intake(); return service,None
            controller=Service(root); controller.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
            worker=AutoWorker(factory,root)
            try:
                worker.start(); self.assertTrue(entered.wait(5)); worker.stop(controller); release.set(); worker.thread.join(timeout=5)
                self.assertFalse(worker.thread.is_alive())
                self.assertEqual(controller.graph.db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('queued',))
                self.assertIsNone(controller.graph.db.execute("SELECT 1 FROM manual_assignments WHERE run='r'").fetchone())
            finally:
                release.set()
                if worker.thread: worker.thread.join(timeout=5)
                controller.lifecycle.close(); controller.close()

    def test_stop_fences_assignment_while_dispatch_is_still_running(self):
        import threading
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); entered=threading.Event(); release=threading.Event(); claims=[]
            def factory():
                service=Service(root); service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self): service.create('r','engineering','task'); service.enqueue('r')
                class Dispatcher:
                    def step(self,*args): claims.extend(args[1:]); entered.set(); release.wait(5); return {'status':'blocked'}
                service.task_intake=Intake(); service.stage_dispatcher=Dispatcher(); return service,None
            controller=Service(root); controller.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
            worker=AutoWorker(factory,root)
            try:
                worker.start(); self.assertTrue(entered.wait(5))
                worker.stop(controller)
                self.assertTrue(worker.thread.is_alive())
                self.assertEqual(controller.graph.db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('cancelling',))
                self.assertEqual(controller.graph.db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('cancelled',))
                self.assertEqual(controller.lifecycle.status('r')['status'],'pending')
                self.assertFalse(controller.queue.may_start('r',claims[0]))
                from runner.graph import GraphError
                with self.assertRaises(GraphError): controller.graph._lease('r',claims[1])
            finally:
                release.set()
                if worker.thread: worker.thread.join(timeout=5)
                controller.lifecycle.close(); controller.close()

    def test_uncertain_dispatch_stops_loop_and_fences_job(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);calls=[]
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):calls.append(1);return {'stage':'asset_production','status':'uncertain'}
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();return service,None
            worker=AutoWorker(factory,root);worker.start();worker.thread.join(timeout=5)
            self.assertFalse(worker.thread.is_alive());self.assertEqual(worker.status()['status'],'inspection_required');self.assertEqual(len(calls),1)
            db=sqlite3.connect(root/'state.db')
            try:self.assertEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('cancelling',))
            finally:db.close()
    def test_atomic_renewal_requires_both_claims_and_active_manual_fence(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); lifecycle=ManualLifecycle(root+'/state.db','worker','inc')
            try:
                service.create('r','engineering','task'); service.enqueue('r'); queue=lifecycle.claim(); claim=service.claim('r')
                before=service.graph.db.execute('SELECT expires FROM jobs WHERE run=?',('r',)).fetchone()[0]
                with self.assertRaises(AutoWorkerError): renew_leases(root+'/state.db','r',queue['token'],'wrong',120)
                self.assertEqual(service.graph.db.execute('SELECT expires FROM jobs WHERE run=?',('r',)).fetchone()[0],before)
                renew_leases(root+'/state.db','r',queue['token'],claim,120)
                job=service.graph.db.execute('SELECT expires FROM jobs WHERE run=?',('r',)).fetchone()[0]
                graph=service.graph.db.execute('SELECT expires FROM graph_claims WHERE run=?',('r',)).fetchone()[0]
                self.assertEqual(job,graph); self.assertGreater(job,before)
                service.cancel_job('r')
                with self.assertRaises(AutoWorkerError): renew_leases(root+'/state.db','r',queue['token'],claim,120)
            finally: lifecycle.close(); service.close()
    def test_accepted_human_decision_wakes_waiting_worker(self):
        import threading
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);processed=threading.Event();version='a'*64
            def factory():
                service=Service(root);service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):service.create('r','marketing','task');service.enqueue('r')
                class Dispatcher:
                    def step(self,*args):return {'stage':'assembly','status':'awaiting_review','version':version}
                class Human:
                    def current(self,run):return {'decision':'rejected'} if processed.is_set() else None
                    def decide(self,*args):processed.set()
                service.task_intake=Intake();service.stage_dispatcher=Dispatcher();service.human_review=Human();return service,None
            worker=AutoWorker(factory,root)
            try:
                worker.start();deadline=time.monotonic()+5
                while worker.status()['status']!='awaiting_review' and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(worker.status()['status'],'awaiting_review')
                worker.request_decision('r',version,'rejected')
                self.assertTrue(processed.wait(2),'accepted decision did not wake the worker')
            finally:
                worker.stop()
                if worker.thread:worker.thread.join(timeout=5)

    def test_automatic_human_decision_requires_current_version(self):
        with tempfile.TemporaryDirectory() as root:
            worker=AutoWorker(lambda:None,Path(root)); version='a'*64
            worker._set(status='awaiting_review',run='r',version=version)
            with self.assertRaises(AutoWorkerError): worker.request_decision('r','b'*64,'approved')
            self.assertTrue(worker.request_decision('r',version,'approved')['pending'])
            with self.assertRaises(AutoWorkerError): worker.request_decision('r',version,'rejected')
            self.assertNotIn('token',str(worker.status()))

    def test_rejected_human_decision_does_not_complete_job(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); version='a'*64
            def factory():
                service=Service(root); service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self):
                        service.create('r','marketing','task'); service.enqueue('r')
                        service.graph.db.execute("INSERT INTO graph_stages VALUES(?,?,?,?,?)",('r','assembly','awaiting_review',version,1))
                class Dispatcher:
                    def step(self,run,queue,claim): return {'stage':'assembly','status':'awaiting_review'}
                class Human:
                    def current(self,run): return {'version':version,'decision':'rejected'}
                service.task_intake=Intake(); service.stage_dispatcher=Dispatcher(); service.human_review=Human()
                return service,None
            worker=AutoWorker(factory,root); worker.start(); deadline=time.monotonic()+5
            while worker.status()['status'] not in ('revision_required','inspection_required') and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(worker.status()['status'],'revision_required')
            db=sqlite3.connect(root/'state.db')
            try: self.assertEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('running',))
            finally: db.close()
            worker.stop(); worker.thread.join(timeout=5); self.assertFalse(worker.thread.is_alive())

    def test_automatic_pr_action_keeps_claims_internal(self):
        with tempfile.TemporaryDirectory() as root:
            worker=AutoWorker(lambda:None,Path(root)); worker._set(status='awaiting_approval',run='r')
            with self.assertRaises(AutoWorkerError): worker.request_action('r','pr_execute',{'queue_claim':'forged','proposal':'x'})
            with self.assertRaises(AutoWorkerError): worker.request_action('r','merge',{})
            ticket=worker.request_action('r','pr_propose',{'kind':'push','parameters':{'expected_remote':None}})
            self.assertTrue(ticket['pending']); self.assertNotIn('token',str(worker.status()))
            with self.assertRaises(AutoWorkerError): worker.request_action('r','pr_execute',{'proposal':'x'})

    def test_one_task_loop_waits_at_blocker_and_fences_on_stop(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            def factory():
                service=Service(root); service.lifecycle=ManualLifecycle(str(root/'state.db'),'worker','inc')
                class Intake:
                    config={'run':'r'}
                    def submit_configured(self): service.create('r','engineering','task'); service.enqueue('r')
                class Dispatcher:
                    def step(self,run,queue,claim): return {'stage':'implement','status':'blocked','reason':'fixture blocker'}
                service.task_intake=Intake(); service.stage_dispatcher=Dispatcher()
                return service,None
            worker=AutoWorker(factory,root); self.assertTrue(worker.start()['started'])
            deadline=time.monotonic()+5
            while worker.status()['status'] not in ('blocked','inspection_required') and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(worker.status()['status'],'blocked'); worker.stop(); worker.thread.join(timeout=5)
            self.assertFalse(worker.thread.is_alive()); self.assertEqual(worker.status()['status'],'inspection_required')
            db=sqlite3.connect(root/'state.db')
            try:
                self.assertEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('cancelling',))
                self.assertEqual(db.execute("SELECT state FROM manual_assignments WHERE run='r'").fetchone(),('pending',))
            finally: db.close()
