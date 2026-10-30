import json
import unittest
from runner.structured import parse_marketing_result, ResultError

class StructuredTests(unittest.TestCase):
    def test_rejects_malformed_findings_and_stage_mismatch(self):
        good={'stage':'canonical_review','status':'passed','input_versions':['v1'],'outputs':['out'],'findings':[]}
        self.assertEqual(parse_marketing_result(json.dumps(good),'canonical_review')['status'],'passed')
        with self.assertRaises(ResultError): parse_marketing_result(json.dumps(good),'other')
        good['findings']=[{'severity':'nonblocking'}]
        with self.assertRaises(ResultError): parse_marketing_result(json.dumps(good),'canonical_review')
