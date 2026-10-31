import sqlite3
import tempfile
import unittest

from runner.metrics import snapshot, prometheus_text
from runner.service import Service


class MetricsTests(unittest.TestCase):
    def test_snapshot_aggregates_both_workflows_without_private_labels(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                for run,flow in [('private-eng','engineering'),('private-marketing','marketing')]:
                    service.create(run,flow,'private-task-'+run); service.enqueue(run)
                    token=service.claim(run)
                    service.graph.finish(run,'intake',service.graph.prepare(run,'intake',token),'passed','private-artifact',token)
                service.actions.approve('private-eng','private-action','private-version','private-actor')
                service.graph.db.execute("INSERT INTO graph_attempts VALUES(?,?,?,?,?)",('private-marketing','asset_production','private-version',2,'uncertain'))
                service.graph.db.execute("INSERT INTO quota_pauses VALUES(?,?,?)",('private-scope','private-marketing','asset_production'))
                service.graph.db.execute("INSERT INTO cache_audit(schema_version,run,stage,status,input_hash,output_hash,created) VALUES(1,'private-marketing','asset_production','reused','private-input','private-output',1)")
                for state in ('requested','uncertain'):
                    service.graph.db.execute("INSERT INTO execution_launch_audit(schema_version,run,role,execution,status,model,created) VALUES(1,'private-eng','implement','private-launch',?,'private-model',1)",(state,))
                service.graph.db.execute("INSERT INTO execution_launch_audit(schema_version,run,role,execution,status,model,created) VALUES(1,'private-marketing','secret-role','private-launch','secret-state','private-model',1)")
                service.graph.db.execute("INSERT INTO execution_launch_audit(schema_version,run,role,execution,status,model,created) VALUES(1,'private-marketing','source_review','private-review','requested','private-model',1)")
                service.graph.db.execute("INSERT INTO tool_audit(schema_version,run,role,session,turn,tool_kind,status,input_present,output_present,evidence_ref,created) VALUES(1,'private-marketing','source_review','private-session','private-turn','mcpToolCall','completed',1,1,'private-receipt',1)")
                service.graph.db.execute("INSERT INTO tool_audit(schema_version,run,role,session,turn,tool_kind,status,input_present,output_present,evidence_ref,created) VALUES(1,'private-marketing','private-role','private-session','private-turn','private-tool','private-status',1,1,'private-receipt',1)")
                db=service.graph.db
                metrics=snapshot(db,now=1); text=prometheus_text(metrics)
                self.assertIn('workflow_snapshot_timestamp_seconds 1',text)
                self.assertIn('workflow_stages{workflow="engineering",stage="intake",status="passed"} 1',text)
                self.assertIn('workflow_stages{workflow="marketing",stage="intake",status="passed"} 1',text)
                self.assertIn('workflow_retried_inputs{workflow="marketing",stage="asset_production",status="uncertain"} 1',text)
                self.assertIn('workflow_quota_pauses{workflow="marketing"} 1',text)
                self.assertIn('workflow_cache_reuses{workflow="marketing",stage="asset_production"} 1',text)
                self.assertIn('workflow_launch_observations{workflow="engineering",role="implement",status="uncertain"} 1',text)
                self.assertNotIn('workflow_launch_observations{workflow="engineering",role="implement",status="requested"}',text)
                self.assertIn('workflow_launch_observations{workflow="marketing",role="source_review",status="requested"} 1',text)
                self.assertIn('workflow_launch_observations{workflow="marketing",role="unavailable",status="unavailable"} 1',text)
                self.assertIn('workflow_observed_tools{workflow="marketing",role="source_review",tool="mcpToolCall",status="completed"} 1',text)
                self.assertIn('workflow_observed_tools{workflow="marketing",role="unavailable",tool="unavailable",status="unavailable"} 1',text)
                self.assertNotIn('private',text)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM graph_stages').fetchone()[0],2)
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
