import sqlite3
import tempfile
import unittest

from runner.metrics import snapshot, prometheus_text
from runner.service import Service


class MetricsTests(unittest.TestCase):
    def test_wait_observation_expires_with_claim_or_changed_dependency(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('run','engineering','task'); service.enqueue('run')
                db=service.graph.db
                db.execute("UPDATE jobs SET status='running',expires=20 WHERE run='run'")
                db.execute("INSERT INTO graph_claims VALUES('task','run','private-token',20)")
                db.execute("INSERT INTO claim_audit(schema_version,run,task_id,status,created) VALUES(1,'run','task','claimed',1)")
                db.execute("INSERT INTO graph_stages VALUES('run','qa','passed','private-version',7)")
                db.execute("INSERT INTO approval_waits VALUES('run','open_draft','qa','private-version',7,1)")
                sample='workflow_stages{workflow="engineering",stage="open_draft",status="awaiting_approval"} 1'
                self.assertIn(sample,prometheus_text(snapshot(db,now=10)))
                self.assertNotIn(sample,prometheus_text(snapshot(db,now=20)))
                db.execute("INSERT INTO claim_audit(schema_version,run,task_id,status,created) VALUES(1,'run','task','claimed',2)")
                self.assertNotIn(sample,prometheus_text(snapshot(db,now=10)))
                db.execute("UPDATE approval_waits SET claim_id=2 WHERE run='run'")
                db.execute("UPDATE graph_stages SET revision=8 WHERE run='run' AND stage='qa'")
                self.assertNotIn(sample,prometheus_text(snapshot(db,now=10)))
                db.execute("UPDATE graph_stages SET revision=7 WHERE run='run' AND stage='qa'")
                db.execute("INSERT INTO graph_stages VALUES('run','open_draft','passed','private-result',9)")
                self.assertNotIn(sample,prometheus_text(snapshot(db,now=10)))
            finally: service.close()


    def test_expired_leases_and_unknown_values_do_not_expose_tokens(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('run','engineering','task'); service.enqueue('run')
                service.graph.db.execute("UPDATE jobs SET status='running',token='secret',expires=5 WHERE run='run'")
                service.graph.db.execute("INSERT INTO graph_stages VALUES('run','secret-stage','secret-status','secret-version',1)")
                text=prometheus_text(snapshot(service.graph.db,now=6))
                self.assertIn('workflow_expired_leases 1',text)
                self.assertIn('stage="unavailable",status="unavailable"',text)
                self.assertNotIn('secret',text)
                self.assertEqual(service.queue.get('run')['status'],'uncertain')
            finally: service.close()

    def test_missing_state_fails_instead_of_reporting_zero_and_labels_are_validated(self):
        db=sqlite3.connect(':memory:',isolation_level=None)
        try:
            with self.assertRaises(sqlite3.OperationalError): snapshot(db,now=1)
            self.assertFalse(db.in_transaction)
            with self.assertRaises(ValueError): prometheus_text({('workflow_jobs',(('status','secret'),)):1})
        finally: db.close()
