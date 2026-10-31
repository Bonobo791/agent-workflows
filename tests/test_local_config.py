import hashlib
import tempfile
import unittest
from pathlib import Path
from runner.local_config import local_supervisor_factory, LocalConfigError
from runner.service import Service

class LocalConfigTests(unittest.TestCase):
    def test_pinned_binary_and_private_marketing_workspaces(self):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_bytes(b'synthetic');binary.chmod(0o700)
            config={'codex_executable':str(binary),'codex_sha256':hashlib.sha256(b'synthetic').hexdigest(),'disabled_integrations':{'mcp_servers':[],'plugins':[]}}
            service=Service(root)
            try:
                service.create('run','marketing','task')
                factory=local_supervisor_factory(config,{'HOME':root,'ACCESS_TOKEN':'secret'},root)
                supervisor=factory(service)
                self.assertTrue(supervisor.verify_policy())
                self.assertNotIn('ACCESS_TOKEN',supervisor.environment)
                workspace=Path(supervisor.workspace_for('run','canonical_draft'))
                self.assertTrue(workspace.is_dir());self.assertEqual(workspace.stat().st_mode&0o777,0o700)
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=1) as pool:self.assertEqual(pool.submit(supervisor.workspace_for,'run','canonical_draft').result(),str(workspace))
                with self.assertRaises(LocalConfigError): supervisor.workspace_for('run','../escape')
                binary.write_bytes(b'changed');self.assertFalse(supervisor.verify_policy())
                service.create('eng','engineering','task')
                with self.assertRaises(LocalConfigError): supervisor.workspace_for('eng','implement')
            finally:service.close()
    def test_rejects_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(LocalConfigError): local_supervisor_factory({'codex_executable':'relative','codex_sha256':'x','disabled_integrations':{}},{'HOME':root},root)
            binary=Path(root)/'codex';binary.write_bytes(b'synthetic');binary.chmod(0o700)
            config={'codex_executable':str(binary),'codex_sha256':hashlib.sha256(b'synthetic').hexdigest(),'disabled_integrations':None}
            with self.assertRaises(LocalConfigError): local_supervisor_factory(config,{'HOME':root},root)
