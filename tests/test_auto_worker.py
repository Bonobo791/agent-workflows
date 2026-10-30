import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from runner.auto_worker import AutoWorker, AutoWorkerError, renew_leases
from runner.manual_lifecycle import ManualLifecycle
from runner.service import Service

class AutoWorkerTests(unittest.TestCase):
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
