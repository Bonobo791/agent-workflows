import os
import tempfile
import unittest
from runner.artifacts import ArtifactStore, ArtifactError

class ArtifactTests(unittest.TestCase):
    def test_content_addressed_storage_detects_tampering(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root)
            digest=store.put(b'synthetic asset')
            self.assertEqual(store.get(digest),b'synthetic asset')
            self.assertEqual(store.put(b'synthetic asset'),digest)
            path=os.path.join(root,digest)
            os.chmod(path,0o600)
            with open(path,'wb') as f: f.write(b'changed')
            with self.assertRaises(ArtifactError): store.get(digest)
    def test_rejects_invalid_digest_and_unsafe_directory(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root)
            with self.assertRaises(ArtifactError): store.get('../escape')
            os.chmod(root,0o755)
            with self.assertRaises(ArtifactError): store.put(b'data')
