import unittest
from workflows.engineering.intake import readiness

class IntakeTests(unittest.TestCase):
    def test_ready_leaf_is_accepted(self):
        self.assertEqual(readiness({'leaf': True, 'ready': True, 'dependencies_complete': True, 'owner': None, 'existing_work': [], 'due': None}, '2026-10-30'), [])
    def test_unknown_and_conflicting_inputs_block(self):
        issues = readiness({'leaf': True, 'ready': True, 'dependencies_complete': None, 'owner': 'someone', 'existing_work': ['branch'], 'due': '2026-10-29'}, '2026-10-30')
        self.assertEqual(set(issues), {'dependencies unknown', 'already owned', 'existing work requires review', 'overdue requires review'})
