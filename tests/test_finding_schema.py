import unittest
from workflows.marketing.gates import canonical_issues

class FindingSchemaTests(unittest.TestCase):
    def setUp(self):
        self.finding={'id':'F-1','severity':'nonblocking','description':'Clarify a claim', 'location':'paragraph 2','evidence':'source conflicts','remedy':'revise claim','resolution':'open'}
        self.reviews={r:{'status':'passed','session_id':r,'findings':[]} for r in ('editorial','factual','seo_geo')}
    def issues(self, finding):
        self.reviews['factual']['findings']=[finding]
        return canonical_issues(self.reviews,['writer'],[],'writer')
    def test_valid_nonblocking_open_finding_does_not_block(self):
        self.assertEqual([], self.issues(self.finding))
    def test_missing_and_invalid_required_fields_block(self):
        for field in self.finding:
            with self.subTest(field=field, error='missing'):
                value=dict(self.finding); del value[field]; self.assertTrue(self.issues(value))
            with self.subTest(field=field, error='type'):
                value=dict(self.finding); value[field]=[]; self.assertTrue(self.issues(value))
            with self.subTest(field=field, error='blank'):
                value=dict(self.finding); value[field]='  '; self.assertTrue(self.issues(value))
    def test_unknown_fields_and_dispositions_block(self):
        for extra in ({'other':'x'}, {'resolution':'ignored'}, {'severity':'critical'}):
            value={**self.finding, **extra}; self.assertTrue(self.issues(value))
    def test_blocking_open_and_resolved_finding(self):
        value={**self.finding,'severity':'blocking'}
        self.assertTrue(self.issues(value))
        value['resolution']='resolved'; self.assertEqual([], self.issues(value))

if __name__=='__main__': unittest.main()
