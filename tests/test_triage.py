import unittest
from workflows.engineering.triage import review_snapshot, TriageError

class TriageTests(unittest.TestCase):
    def test_requires_same_commit_all_comments_and_checks(self):
        snapshot={'head':'abc','checks':[{'sha':'old','status':'success'}], 'comments':[{'id':'1'}], 'unresolved_change_requests':[]}
        with self.assertRaises(TriageError): review_snapshot(snapshot, 'abc', {'1':'invalid'})
        snapshot['checks']=[{'sha':'abc','status':'success'}]
        with self.assertRaises(TriageError): review_snapshot(snapshot,'abc',{})
        self.assertTrue(review_snapshot(snapshot,'abc',{'1':'invalid'}))
    def test_valid_comment_blocks_and_unknown_category_rejected(self):
        snapshot={'head':'abc','checks':[{'sha':'abc','status':'success'}], 'comments':[{'id':'1'}], 'unresolved_change_requests':[]}
        self.assertFalse(review_snapshot(snapshot,'abc',{'1':'valid'}))
        with self.assertRaises(TriageError): review_snapshot(snapshot,'abc',{'1':'execute'})
