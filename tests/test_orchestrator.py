import tempfile
import unittest
from runner.store import Store
from runner.orchestrator import Orchestrator, StageBlocked

class FakeSpecialist:
    def __init__(self): self.calls=[]; self.ok=True
    def run(self, stage, inputs, session_id):
        self.calls.append((stage, inputs, session_id))
        return {'passed':self.ok, 'session_id':session_id or 'new-session'}

class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name+'/db'); self.store.create_run('r','t','engineering'); self.fake=FakeSpecialist(); self.o=Orchestrator(self.store,self.fake,max_attempts=2)
    def tearDown(self): self.store.db.close(); self.tmp.cleanup()
    def test_green_is_reused_and_reviewer_is_fresh(self):
        self.o.execute('r','plan','plan-v1',{})
        self.o.execute('r','implement','sha',{})
        self.o.execute('r','test','sha',{})
        self.o.execute('r','qa','sha',{})
        self.o.execute('r','qa','sha',{})
        self.assertEqual(len(self.fake.calls), 4)
        self.assertIsNone(self.fake.calls[-1][2])
    def test_retries_bounded_and_implementation_resumes(self):
        self.o.execute('r','plan','p',{})
        self.store.session('r','implement','old-session')
        self.fake.ok=False
        for _ in range(2): self.assertFalse(self.o.execute('r','implement','sha',{}))
        with self.assertRaises(StageBlocked): self.o.execute('r','implement','sha',{})
        self.assertEqual(self.fake.calls[-1][2], 'old-session')
