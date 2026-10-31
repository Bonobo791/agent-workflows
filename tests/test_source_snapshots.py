import hashlib,json,tempfile,unittest
from unittest.mock import patch
from runner.service import Service
from runner.source_fetch import SourceFetchError
from runner.source_snapshots import SourceSnapshots
from tests.source_fixture import REGISTER

class Fetcher:
    hosts={'example.invalid'};urls={'https://example.invalid/evidence'};max_bytes=1024;timeout=5
    def fetch(self,url):
        text='retrieved primary evidence'
        return {'url':url,'content_type':'text/plain','content':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}

class SnapshotTests(unittest.TestCase):
    def test_snapshot_binds_run_register_draft_policy_and_age(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','marketing','task');claim=service.claim('r')
                register=service.artifacts.put(json.dumps(REGISTER).encode());draft=service.artifacts.put(b'draft')
                snapshots=SourceSnapshots(service,Fetcher());bundle=snapshots.capture('r',claim,register,draft,lambda:False)
                self.assertTrue(snapshots.verify('r',register,draft,bundle))
                self.assertEqual(snapshots.contents(bundle)[0]['content'],'retrieved primary evidence')
                self.assertFalse(snapshots.verify('other',register,draft,bundle));self.assertFalse(snapshots.verify('r',register,None,bundle))
                with patch.object(snapshots.fetcher,'max_bytes',256): self.assertFalse(snapshots.verify('r',register,draft,bundle))
                with patch('runner.source_snapshots.time.time',return_value=10**11): self.assertFalse(snapshots.verify('r',register,draft,bundle))
                other=Fetcher();other.urls={'https://other.invalid/'}
                self.assertFalse(SourceSnapshots(service,other).verify('r',register,draft,bundle))
                with self.assertRaises(SourceFetchError): snapshots.capture('r',claim,register,draft,lambda:True)
            finally:service.close()
