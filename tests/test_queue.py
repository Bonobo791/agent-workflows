import tempfile
import unittest
from runner.queue import JobQueue, QueueError

class QueueTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.time=[0]; self.q=JobQueue(self.tmp.name+'/db',clock=lambda:self.time[0])
    def tearDown(self): self.q.close(); self.tmp.cleanup()
    def test_one_job_at_a_time_and_restart_recovery_is_uncertain(self):
        self.q.enqueue('one'); self.q.enqueue('two'); token=self.q.claim('worker',10)
        self.assertEqual(token['run'],'one'); self.assertIsNone(self.q.claim('other',10))
        self.time[0]=11
        self.assertIsNone(self.q.claim('other',10))
        self.assertEqual(self.q.get('one')['status'],'uncertain')
    def test_cancel_blocks_completion_until_supervisor_confirms_stop(self):
        self.q.enqueue('one'); claim=self.q.claim('worker',10); self.q.cancel('one')
        with self.assertRaises(QueueError): self.q.finish('one',claim['token'])
        self.assertEqual(self.q.get('one')['status'],'cancelling')
        self.q.confirm_stopped('one',claim['token'],verify_stopped=lambda run:True)
        self.assertEqual(self.q.get('one')['status'],'cancelled')
    def test_expired_claim_cannot_finish(self):
        self.q.enqueue('one'); claim=self.q.claim('worker',10); self.time[0]=11
        with self.assertRaises(QueueError): self.q.finish('one',claim['token'])

    def test_uncertain_job_needs_confirmed_stop_and_operator_disposition(self):
        self.q.enqueue('one'); self.q.enqueue('two'); self.q.claim('worker',5); self.time[0]=6
        self.assertIsNone(self.q.claim('other',5))
        with self.assertRaises(QueueError): self.q.resolve_uncertain('one','queued',lambda run:False)
        self.q.resolve_uncertain('one','failed',lambda run:True)
        self.assertEqual(self.q.claim('other',5)['run'],'two')

    def test_status_reports_expired_worker_as_uncertain(self):
        self.q.enqueue('one'); self.q.claim('worker',5); self.time[0]=6
        self.assertEqual(self.q.get('one')['status'],'uncertain')

    def test_finish_requires_trusted_terminal_verification(self):
        self.q.enqueue('one'); claim=self.q.claim('worker',10)
        with self.assertRaises(QueueError): self.q.finish('one',claim['token'])
        self.q.finish('one',claim['token'],verify_completed=lambda run: True)
        self.assertEqual(self.q.get('one')['status'],'completed')
