import tempfile
import unittest
from runner.artifacts import ArtifactStore
from runner.verification import verify_references, VerificationError

class VerificationTests(unittest.TestCase):
    def test_checks_exact_inputs_and_real_output_hashes(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root); ref=store.put(b'synthetic output')
            result={'input_versions':['source-v1'],'outputs':[ref]}
            self.assertEqual(verify_references(store,result,['source-v1']),[ref])
            with self.assertRaises(VerificationError): verify_references(store,result,['source-v2'])
            with self.assertRaises(VerificationError): verify_references(store,{'input_versions':['source-v1'],'outputs':['0'*64]},['source-v1'])
