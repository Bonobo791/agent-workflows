import tempfile
import unittest
from runner.store import Store, Conflict

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(self.tmp.name + '/state.db')
        self.s.create_run('r1', 'task1', 'engineering')
    def tearDown(self): self.s.db.close(); self.tmp.cleanup()
    def test_exclusive_claim_and_recovery(self):
        self.s.claim('r1', 'worker', 10, now=0)
        with self.assertRaises(Conflict): self.s.claim('r1', 'other', 10, now=5)
        self.s.claim('r1', 'other', 10, now=11)
    def test_gates_and_versioned_approval(self):
        with self.assertRaises(Conflict): self.s.advance('r1', 'implement', 'abc')
        self.s.advance('r1', 'plan', 'a')
        self.s.advance('r1', 'implement', 'abc')
        self.s.advance('r1', 'test', 'abc')
        self.s.advance('r1', 'qa', 'abc')
        with self.assertRaises(Conflict): self.s.advance('r1', 'open_draft', 'abc')
        self.s.approve('r1', 'open_draft', 'old', 'reviewer')
        with self.assertRaises(Conflict): self.s.advance('r1', 'open_draft', 'abc')
        self.s.approve('r1', 'open_draft', 'abc', 'reviewer')
        self.s.advance('r1', 'open_draft', 'abc')
    def test_changed_version_invalidates_qa(self):
        for stage, version in [('plan','a'),('implement','v1'),('test','v1'),('qa','v1')]: self.s.advance('r1',stage,version)
        self.s.advance('r1','implement','v2')
        self.s.approve('r1','open_draft','v2','reviewer')
        with self.assertRaises(Conflict): self.s.advance('r1','open_draft','v2')
    def test_sessions_and_cancel_persist(self):
        self.s.session('r1','implement','session-1')
        self.s.cancel('r1')
        reopened = Store(self.tmp.name + '/state.db')
        self.assertEqual(reopened.get_session('r1','implement'), 'session-1')
        reopened.db.close()
        with self.assertRaises(Conflict): self.s.advance('r1','plan','a')
