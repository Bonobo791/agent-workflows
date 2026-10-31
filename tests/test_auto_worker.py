import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from runner.auto_worker import AutoWorker, AutoWorkerError, renew_leases
from runner.manual_lifecycle import ManualLifecycle
from runner.service import Service

class AutoWorkerTests(unittest.TestCase):
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
