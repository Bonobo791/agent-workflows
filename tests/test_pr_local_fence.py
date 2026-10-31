import tempfile
import unittest
from types import SimpleNamespace
from runner.manual_lifecycle import ManualLifecycle
from runner.pr_bridge import PRBridge,PRBridgeError
from runner.service import Service
from runner.worker import manual_fence_ready

class LocalPRFenceTests(unittest.TestCase):
    def test_local_authority_requires_live_manual_fence(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root);service.state_db_path=root+'/state.db';service.lifecycle=ManualLifecycle(service.state_db_path,'worker','inc')
            service.create('run','engineering','task');service.enqueue('run');queue=service.lifecycle.claim();claim=service.claim('run')
            service.model_supervisor=SimpleNamespace(fence=lambda run:manual_fence_ready(service,run))
            class Authority:
                http=object()
                def ready(self):return True
                def observe(self,*args):return {'quiescent':False,'outcome':'unknown'}
            service.pr_authority=Authority();service.task_intake=SimpleNamespace(config={'run':'run','workflow':'engineering','github_owner':'owner','github_repo':'repo','github_base':'main'},revalidate=lambda run:True)
            try:
                bridge=PRBridge(service);loop=bridge._loop('run',queue['token'],claim);loop.ledger.db.close()
                service.model_supervisor.fence=lambda run:False
                with self.assertRaises(PRBridgeError):bridge._loop('run',queue['token'],claim)
            finally:service.lifecycle.close();service.close()
