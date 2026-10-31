import unittest
from workflows.engineering.intake import readiness

class IntakeTests(unittest.TestCase):
    def test_falsy_owner_and_invalid_due_are_not_unassigned_or_unscheduled(self):
        task={'leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
        for invalid in ('',False,0,'20261030'):
            with self.subTest(owner=invalid): self.assertIn('already owned',readiness({**task,'owner':invalid},'2026-10-30'))
            with self.subTest(due=invalid): self.assertIn('invalid due date',readiness({**task,'due':invalid},'2026-10-30'))
    def test_ready_leaf_is_accepted(self):
        self.assertEqual(readiness({'leaf': True, 'ready': True, 'dependencies_complete': True, 'owner': None, 'existing_work': [], 'due': None}, '2026-10-30'), [])
    def test_unknown_and_conflicting_inputs_block(self):
        issues = readiness({'leaf': True, 'ready': True, 'dependencies_complete': None, 'owner': 'someone', 'existing_work': ['branch'], 'due': '2026-10-29'}, '2026-10-30')
        self.assertEqual(set(issues), {'dependencies unknown', 'already owned', 'existing work requires review', 'overdue requires review'})
