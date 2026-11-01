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
                    service.create('r',workflow,'task'); service.enqueue('r'); queue=service.queue.claim('worker',60)
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
