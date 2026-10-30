import copy
import json
import tempfile
import unittest
from runner.service import Service
from runner.source_register import verify, SourceError
from tests.source_fixture import REGISTER, verifier

class SourceRegisterTests(unittest.TestCase):
    def test_source_checks_require_complete_independent_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                ref=service.artifacts.put(json.dumps(REGISTER).encode())
                with self.assertRaises(SourceError): verify(service.artifacts,ref,None)
                self.assertTrue(verify(service.artifacts,ref,verifier))
                def unsupported(register,reference):
                    proof=verifier(register,reference); proof['checks'][0]['supported']=False; return proof
                def missing(register,reference):
                    proof=verifier(register,reference); proof['checks']=[]; return proof
                for source in (unsupported,missing):
                    with self.assertRaises(SourceError): verify(service.artifacts,ref,source)
                bad=copy.deepcopy(REGISTER); bad['claims'][0]['source_ids']=['missing']
                bad_ref=service.artifacts.put(json.dumps(bad).encode())
                with self.assertRaises(SourceError): verify(service.artifacts,bad_ref,verifier)
            finally: service.close()
