import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from runner.audit_health import publish, read_metrics


class AuditHealthTests(unittest.TestCase):
    def test_success_failure_and_corrupt_status(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(read_metrics(root,now=100),'workflow_audit_delivery_configured 1\n')
            publish(root,False,now=90)
            self.assertEqual(read_metrics(root,now=100),'workflow_audit_delivery_configured 1\nworkflow_audit_delivery_last_attempt_timestamp_seconds 90\nworkflow_audit_delivery_last_attempt_success 0\n')
            publish(root,True,now=100)
            self.assertIn('workflow_audit_delivery_last_attempt_success 1\n',read_metrics(root,now=100))
            path=Path(root)/'audit-delivery-health.json'; path.write_text(json.dumps({'timestamp':1000,'success':True})); path.chmod(0o600)
            self.assertEqual(read_metrics(root,now=100),'workflow_audit_delivery_configured 1\n')
            path.unlink(); path.symlink_to('/dev/null')
            self.assertEqual(read_metrics(root,now=100),'workflow_audit_delivery_configured 1\n')
    def test_publish_restores_owner_read_permission_under_restrictive_umask(self):
        import os
        with tempfile.TemporaryDirectory() as root:
            previous=os.umask(0o777)
            try:publish(root,True,now=100)
            finally:os.umask(previous)
            self.assertEqual((Path(root)/'audit-delivery-health.json').stat().st_mode&0o777,0o600)
            self.assertIn('last_attempt_success 1',read_metrics(root,now=100))

    def test_symlink_loop_runtime_error_reports_unavailable_health(self):
        with tempfile.TemporaryDirectory() as root,patch('runner.audit_health.Path.resolve',side_effect=RuntimeError('symlink loop')):
            self.assertEqual(read_metrics(root),'workflow_audit_delivery_configured 1\n')

    def test_command_records_failure_and_success_without_private_data(self):
        from runner.audit_delivery import main
        with tempfile.TemporaryDirectory() as root:
            args=['audit_delivery','--source','/unused','--cursor','/unused','--key-file','/unused',
                  '--endpoint','http://127.0.0.1','--table','graph_audit','--health-directory',root]
            with patch.object(sys,'argv',args), patch('runner.audit_delivery.deliver_tables',return_value={'graph_audit':{'status':'unavailable','cursor':0}}):
                self.assertEqual(main(),1)
            self.assertIn('workflow_audit_delivery_last_attempt_success 0',read_metrics(root))
            publish(root,True)
            with patch.object(sys,'argv',args), patch('runner.audit_delivery.deliver_tables',side_effect=RuntimeError('private')):
                with self.assertRaises(RuntimeError): main()
            self.assertIn('workflow_audit_delivery_last_attempt_success 0',read_metrics(root))
            self.assertNotIn('private',read_metrics(root))
            with patch.object(sys,'argv',args), patch('runner.audit_delivery.deliver_tables',return_value={'graph_audit':{'status':'empty','cursor':0}}):
                self.assertEqual(main(),0)
            self.assertIn('workflow_audit_delivery_last_attempt_success 1',read_metrics(root))

    def test_rejects_nonprivate_directory_and_invalid_status(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root).chmod(0o755)
            with self.assertRaises(ValueError): publish(root,True,now=1)
            self.assertEqual(read_metrics(root,now=1),'workflow_audit_delivery_configured 1\n')
            Path(root).chmod(0o700)
            with self.assertRaises(ValueError): publish(root,'yes',now=1)
