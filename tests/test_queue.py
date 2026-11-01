import sqlite3
import tempfile
import unittest
from runner.queue import JobQueue, QueueError

class QueueTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.time=[0]; self.q=JobQueue(self.tmp.name+'/db',clock=lambda:self.time[0])
    def tearDown(self): self.q.close(); self.tmp.cleanup()
    def test_audit_distinguishes_stop_request_from_confirmed_stop(self):
        self.q.enqueue('one'); claim=self.q.claim('worker',10); self.q.cancel('one')
        self.q.confirm_stopped('one',claim['token'],lambda run:True)
        self.assertEqual(self.q.db.execute('SELECT status FROM job_audit ORDER BY id').fetchall(), [('queued',),('running',),('cancelling',),('cancelled',)])
        self.q.enqueue('two')
        self.q.db.execute("CREATE TRIGGER reject_job_audit BEFORE INSERT ON job_audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.q.claim('worker',10)
        self.assertEqual(self.q.get('two')['status'],'queued')
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

    def test_finish_rejects_claim_expiring_during_verification(self):
        self.q.enqueue('one'); claim=self.q.claim('worker',10)
        def verify(run): self.time[0]=11; return True
        with self.assertRaises(QueueError): self.q.finish('one',claim['token'],verify_completed=verify)
        self.assertEqual(self.q.db.execute("SELECT status FROM jobs WHERE run='one'").fetchone(),('running',))

    def test_finish_requires_durable_completion_audit(self):
        for trigger in ("CREATE TRIGGER reject_completion BEFORE INSERT ON job_audit WHEN NEW.status='completed' BEGIN SELECT RAISE(IGNORE); END",
                        "CREATE TRIGGER reject_completion AFTER INSERT ON job_audit WHEN NEW.status='completed' BEGIN UPDATE job_audit SET status='altered' WHERE id=NEW.id; END"):
            with self.subTest(trigger=trigger):
                self.q.enqueue('one'); claim=self.q.claim('worker',10); self.q.db.execute(trigger)
                with self.assertRaises(QueueError): self.q.finish('one',claim['token'],verify_completed=lambda run: True)
                self.assertEqual(self.q.get('one')['status'],'running')
                self.q.db.execute('DROP TRIGGER reject_completion'); self.q.finish('one',claim['token'],verify_completed=lambda run: True)
                self.q.db.execute('DELETE FROM jobs')

    def test_finish_requires_trusted_terminal_verification(self):
        self.q.enqueue('one'); claim=self.q.claim('worker',10)
        with self.assertRaises(QueueError): self.q.finish('one',claim['token'])
        self.q.finish('one',claim['token'],verify_completed=lambda run: True)
        self.assertEqual(self.q.get('one')['status'],'completed')
