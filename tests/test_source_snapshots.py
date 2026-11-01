import hashlib,json,tempfile,unittest
from unittest.mock import patch
from runner.service import Service
from runner.source_fetch import SourceFetchError
from runner.source_snapshots import SourceSnapshots
from runner.graph import GraphError
from tests.source_fixture import REGISTER

class Fetcher:
    hosts={'example.invalid'};urls={'https://example.invalid/evidence'};max_bytes=1024;timeout=5
    def fetch(self,url,*,cancelled=lambda:False):
        text='retrieved primary evidence'
        return {'url':url,'content_type':'text/plain','content':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}

class SnapshotTests(unittest.TestCase):
    def test_snapshot_does_not_commit_after_claim_loss_or_cancellation_during_storage(self):
        for reason in ('claim','cancel','policy'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as root:
                service=Service(root)
                try:
                    service.create('r','marketing','task'); claim=service.claim('r')
                    register=service.artifacts.put(json.dumps(REGISTER).encode()); snapshots=SourceSnapshots(service,Fetcher())
                    put=service.artifacts.put; cancelled=[False]
                    def interrupt(data):
                        result=put(data)
                        if b'"policy_hash"' in data:
                            if reason=='claim': service.graph.db.execute("UPDATE graph_claims SET expires=0 WHERE run='r'")
                            elif reason=='cancel': cancelled[0]=True
                            else: snapshots.fetcher.max_bytes=512
                        return result
                    with patch.object(service.artifacts,'put',side_effect=interrupt):
                        with self.assertRaises((GraphError,SourceFetchError)): snapshots.capture('r',claim,register,None,lambda:cancelled[0])
                    self.assertEqual(service.graph.db.execute('SELECT count(*) FROM source_snapshots').fetchone()[0],0)
                finally: service.close()
    def test_snapshot_rolls_back_when_claim_expires_during_insert(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','marketing','task'); claim=service.claim('r')
                register=service.artifacts.put(json.dumps(REGISTER).encode()); snapshots=SourceSnapshots(service,Fetcher())
                service.graph.db.create_function('expire_test_claim',0,lambda: setattr(service.graph,'clock',lambda:10**12))
                service.graph.db.execute('CREATE TRIGGER expire_test_claim BEFORE INSERT ON source_snapshots BEGIN SELECT expire_test_claim(); END')
                with self.assertRaises(GraphError): snapshots.capture('r',claim,register,None,lambda:False)
                self.assertEqual(service.graph.db.execute('SELECT count(*) FROM source_snapshots').fetchone()[0],0)
            finally: service.close()
    def test_cancellation_during_fetch_does_not_record_a_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','marketing','task'); claim=service.claim('r')
                register=service.artifacts.put(json.dumps(REGISTER).encode()); flag=[False]
                fetcher=Fetcher()
                def interrupted(url,*,cancelled):
                    flag[0]=True
                    self.assertTrue(cancelled())
                    raise SourceFetchError('cancelled')
                fetcher.fetch=interrupted; snapshots=SourceSnapshots(service,fetcher)
                with self.assertRaises(SourceFetchError): snapshots.capture('r',claim,register,None,lambda:flag[0])
                self.assertEqual(service.graph.db.execute('SELECT COUNT(*) FROM source_snapshots').fetchone()[0],0)
            finally: service.close()
    def test_json_snapshot_is_bound_and_invalid_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','marketing','task');claim=service.claim('r');register=service.artifacts.put(json.dumps(REGISTER).encode())
                fetcher=Fetcher();payload=b'{"rules":["9:16"]}'
                fetcher.fetch=lambda url,**_:{'url':url,'content_type':'application/json','content':payload.decode(),'sha256':hashlib.sha256(payload).hexdigest()}
                snapshots=SourceSnapshots(service,fetcher);bundle=snapshots.capture('r',claim,register,None,lambda:False)
                self.assertTrue(snapshots.verify('r',register,None,bundle))
                payload=b'{invalid'
                with self.assertRaises(SourceFetchError):snapshots.capture('r',claim,register,None,lambda:False)
            finally:service.close()
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
