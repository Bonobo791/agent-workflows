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
