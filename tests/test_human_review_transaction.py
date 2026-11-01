import tempfile
import unittest
from types import SimpleNamespace

from runner.human_review import HumanReview, HumanReviewError
from runner.service import Service


class HumanReviewTransactionTests(unittest.TestCase):
    def test_decision_requires_matching_audit_and_current_queue_fence(self):
        triggers=(
            "CREATE TRIGGER fail_test BEFORE INSERT ON human_review_decisions BEGIN SELECT RAISE(IGNORE); END",
            "CREATE TRIGGER fail_test BEFORE INSERT ON human_review_audit BEGIN SELECT RAISE(IGNORE); END",
            "CREATE TRIGGER fail_test AFTER INSERT ON human_review_audit BEGIN UPDATE human_review_audit SET actor='altered' WHERE id=NEW.id; END",
            "CREATE TRIGGER fail_test AFTER INSERT ON human_review_decisions BEGIN UPDATE jobs SET status='cancelling' WHERE run=NEW.run; END",
            "CREATE TRIGGER fail_test AFTER INSERT ON human_review_decisions BEGIN UPDATE graph_runs SET status='cancelled' WHERE id=NEW.run; END",
            "CREATE TRIGGER fail_test AFTER INSERT ON human_review_decisions BEGIN UPDATE graph_stages SET version='altered' WHERE run=NEW.run AND stage='assembly'; END",
        )
        for trigger in triggers:
            with self.subTest(trigger=trigger), tempfile.TemporaryDirectory() as root:
                service=Service(root)
                try:
                    service.create('r','marketing','task'); service.enqueue('r'); queue=service.queue.claim('worker',60); claim=service.graph.claim('r',60)
                    service.graph.db.execute("INSERT INTO graph_stages VALUES('r','assembly','awaiting_review','package',1)")
                    service.task_intake=SimpleNamespace(revalidate=lambda run:True); service.verify_terminal=lambda run:True
                    review=HumanReview(service); service.graph.db.execute(trigger)
                    with self.assertRaises(HumanReviewError): review.decide('r',queue['token'],claim,'package','approved')
                    self.assertIsNone(review.current('r'))
                    self.assertEqual(service.graph.db.execute('SELECT count(*) FROM human_review_audit').fetchone()[0],0)
                    self.assertEqual(service.graph.db.execute("SELECT status FROM jobs WHERE run='r'").fetchone()[0],'running')
                finally: service.close()

    def test_completion_requires_current_audited_approval(self):
        from runner.queue import QueueError
        for decision in (None, 'rejected', 'approved', 'missing_audit', 'stale_package'):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as root:
                service=Service(root)
                try:
                    service.create('r','marketing','task'); service.enqueue('r'); queue=service.queue.claim('worker',60); claim=service.graph.claim('r',60)
                    service.graph.db.execute("INSERT INTO graph_stages VALUES('r','assembly','awaiting_review','package',1)")
                    service.task_intake=SimpleNamespace(revalidate=lambda run:True); service.verify_terminal=lambda run:True
                    service.human_review=HumanReview(service)
                    if decision is not None:
                        service.human_review.decide('r',queue['token'],claim,'package','rejected' if decision=='rejected' else 'approved')
                    if decision=='missing_audit': service.graph.db.execute('DELETE FROM human_review_audit')
                    if decision=='stale_package': service.graph.db.execute("UPDATE graph_stages SET version='new-package' WHERE run='r' AND stage='assembly'")
                    if decision=='approved':
                        service.finish_job('r',queue['token'])
                        self.assertEqual(service.queue.get('r')['status'],'completed')
                        self.assertEqual(service.graph.db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('completed',))
                        self.assertIsNone(service.graph.db.execute("SELECT 1 FROM graph_claims WHERE run='r'").fetchone())
                    else:
                        with self.assertRaises(QueueError): service.finish_job('r',queue['token'])
                        self.assertEqual(service.queue.get('r')['status'],'running')
                finally: service.close()

    def test_completion_requires_current_graph_claim(self):
        from runner.queue import QueueError
        for condition in ('missing','expired','wrong_task'):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as root:
                service=Service(root)
                try:
                    service.create('r','engineering','task'); service.enqueue('r'); queue=service.queue.claim('worker',60)
                    if condition!='missing':
                        service.claim('r')
                        if condition=='expired': service.graph.db.execute("UPDATE graph_claims SET expires=0 WHERE run='r'")
                        else: service.graph.db.execute("UPDATE graph_claims SET task_id='other' WHERE run='r'")
                    service.verify_terminal=lambda run:True
                    with self.assertRaises(QueueError): service.finish_job('r',queue['token'])
                    self.assertEqual(service.queue.get('r')['status'],'running')
                finally: service.close()

    def test_completion_rolls_back_when_claim_release_audit_is_unavailable(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','engineering','task'); service.enqueue('r'); queue=service.queue.claim('worker',60); service.claim('r')
                service.verify_terminal=lambda run:True
                service.graph.db.execute("CREATE TRIGGER reject_release BEFORE INSERT ON claim_audit WHEN NEW.status='released' BEGIN SELECT RAISE(IGNORE); END")
                with self.assertRaises((sqlite3.Error,RuntimeError)): service.finish_job('r',queue['token'])
                self.assertEqual(service.queue.get('r')['status'],'running')
                self.assertEqual(service.graph.db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('active',))
                self.assertIsNotNone(service.graph.db.execute("SELECT 1 FROM graph_claims WHERE run='r'").fetchone())
            finally: service.close()

    def test_completion_rolls_back_when_graph_state_is_changed_in_transaction(self):
        from runner.queue import QueueError
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','engineering','task'); service.enqueue('r'); queue=service.queue.claim('worker',60); service.claim('r')
                service.verify_terminal=lambda run:True
                service.graph.db.execute("CREATE TRIGGER alter_finish AFTER UPDATE OF status ON graph_runs WHEN NEW.status='completed' BEGIN UPDATE graph_runs SET status='cancelled' WHERE id=NEW.id; END")
                with self.assertRaises(QueueError): service.finish_job('r',queue['token'])
                self.assertEqual(service.queue.get('r')['status'],'running')
                self.assertEqual(service.graph.db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('active',))
                self.assertIsNotNone(service.graph.db.execute("SELECT 1 FROM graph_claims WHERE run='r'").fetchone())
            finally: service.close()

    def test_job_completion_trigger_cannot_change_completed_graph(self):
        from runner.queue import QueueError
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','engineering','task'); service.enqueue('r'); queue=service.queue.claim('worker',60); service.claim('r')
                service.verify_terminal=lambda run:True
                service.graph.db.execute("CREATE TRIGGER alter_graph AFTER UPDATE OF status ON jobs WHEN NEW.status='completed' BEGIN UPDATE graph_runs SET status='cancelled' WHERE id=NEW.run; END")
                with self.assertRaises(QueueError): service.finish_job('r',queue['token'])
                self.assertEqual(service.queue.get('r')['status'],'running')
                self.assertEqual(service.graph.db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('active',))
                self.assertIsNotNone(service.graph.db.execute("SELECT 1 FROM graph_claims WHERE run='r'").fetchone())
            finally: service.close()

    def test_job_audit_failure_rolls_back_graph_completion_and_claim_release(self):
        from runner.queue import QueueError
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','engineering','task'); service.enqueue('r'); queue=service.queue.claim('worker',60); service.claim('r')
                service.verify_terminal=lambda run:True
                service.graph.db.execute("CREATE TRIGGER reject_finish BEFORE INSERT ON job_audit WHEN NEW.status='completed' BEGIN SELECT RAISE(IGNORE); END")
                with self.assertRaises(QueueError): service.finish_job('r',queue['token'])
                self.assertEqual(service.queue.get('r')['status'],'running')
                self.assertEqual(service.graph.db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('active',))
                self.assertIsNotNone(service.graph.db.execute("SELECT 1 FROM graph_claims WHERE run='r'").fetchone())
                self.assertFalse(service.graph.db.execute("SELECT 1 FROM claim_audit WHERE run='r' AND status='released'").fetchone())
            finally: service.close()

    def test_completion_rechecks_task_freshness_after_approval(self):
        from runner.queue import QueueError
        for freshness in (True, False, 'unavailable'):
            with self.subTest(freshness=freshness), tempfile.TemporaryDirectory() as root:
                service=Service(root)
                try:
                    service.create('r','marketing','task'); service.enqueue('r'); queue=service.queue.claim('worker',60); claim=service.graph.claim('r',60)
                    service.graph.db.execute("INSERT INTO graph_stages VALUES('r','assembly','awaiting_review','package',1)")
                    service.task_intake=SimpleNamespace(revalidate=lambda run:True); service.verify_terminal=lambda run:True
                    service.human_review=HumanReview(service)
                    service.human_review.decide('r',queue['token'],claim,'package','approved')
                    def revalidate(run):
                        if freshness=='unavailable': raise RuntimeError('offline')
                        return freshness
                    service.task_intake.revalidate=revalidate
                    if freshness is True:
                        service.finish_job('r',queue['token'])
                        self.assertEqual(service.queue.get('r')['status'],'completed')
                    else:
                        with self.assertRaises(QueueError): service.finish_job('r',queue['token'])
                        self.assertEqual(service.queue.get('r')['status'],'running')
                finally: service.close()

    def test_completion_requires_review_service_only_for_marketing(self):
        from runner.queue import QueueError
        for workflow in ('marketing','engineering'):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as root:
                service=Service(root)
                try:
                    service.create('r',workflow,'task'); service.enqueue('r'); queue=service.queue.claim('worker',60); service.claim('r')
                    service.verify_terminal=lambda run:True
                    if workflow=='marketing':
                        with self.assertRaises(QueueError): service.finish_job('r',queue['token'])
                    else:
                        service.finish_job('r',queue['token'])
                        self.assertEqual(service.queue.get('r')['status'],'completed')
                finally: service.close()

    def test_existing_decision_without_audit_requires_inspection(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','marketing','task'); service.enqueue('r'); queue=service.queue.claim('worker',60); claim=service.graph.claim('r',60)
                service.graph.db.execute("INSERT INTO graph_stages VALUES('r','assembly','awaiting_review','package',1)")
                service.task_intake=SimpleNamespace(revalidate=lambda run:True); service.verify_terminal=lambda run:True
                review=HumanReview(service)
                self.assertEqual(review.decide('r',queue['token'],claim,'package','approved')['decision'],'approved')
                self.assertEqual(review.current('r')['decision'],'approved')
                service.graph.db.execute('DELETE FROM human_review_audit')
                with self.assertRaises(HumanReviewError): review.current('r')
                service.graph.db.execute("UPDATE human_review_decisions SET actor='other'")
                service.graph.db.execute("INSERT INTO human_review_audit(schema_version,run,version,status,actor,created) VALUES(1,'r','package','approved','other',0)")
                with self.assertRaises(HumanReviewError): review.current('r')
            finally: service.close()
