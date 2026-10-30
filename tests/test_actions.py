import tempfile
import unittest
from runner.actions import ActionLedger, ActionConflict

class ActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = ActionLedger(self.tmp.name + '/state.db')
    def tearDown(self): self.ledger.db.close(); self.tmp.cleanup()
    def test_approval_is_exact_and_single_use(self):
        self.ledger.approve('run', 'push', 'sha-a', 'human')
        with self.assertRaises(ActionConflict): self.ledger.reserve('run', 'push', 'sha-b')
        self.ledger.reserve('run', 'push', 'sha-a')
        with self.assertRaises(ActionConflict): self.ledger.reserve('run', 'push', 'sha-a')
    def test_unresolved_attempt_is_not_retried(self):
        self.ledger.approve('run', 'push', 'sha-a', 'human')
        self.ledger.reserve('run', 'push', 'sha-a')
        with self.assertRaises(ActionConflict): self.ledger.approve('run', 'push', 'sha-a', 'human')
        with self.assertRaises(ActionConflict): self.ledger.approve('run', 'push', 'sha-b', 'human')
        self.ledger.resolve('run', 'push', 'sha-a', 'not_executed')
        self.ledger.approve('run', 'push', 'sha-a', 'human')
        self.ledger.reserve('run', 'push', 'sha-a')

    def test_preapproved_versions_cannot_be_reserved_at_same_time(self):
        self.ledger.approve('run','push','v1','reviewer')
        self.ledger.approve('run','push','v2','reviewer')
        self.ledger.reserve('run','push','v1')
        with self.assertRaises(ActionConflict): self.ledger.reserve('run','push','v2')

    def test_uncertain_scope_blocks_other_action_versions(self):
        self.ledger.approve('run','payload-a','v1','reviewer'); self.ledger.approve('run','payload-b','v2','reviewer')
        self.ledger.reserve_scoped('run','payload-a','v1','branch')
        with self.assertRaises(ActionConflict): self.ledger.reserve_scoped('run','payload-b','v2','branch')
        self.ledger.resolve_scoped('run','payload-a','v1','branch','not_executed')
        self.ledger.reserve_scoped('run','payload-b','v2','branch')

    def test_followup_persists_atomically_with_resolution(self):
        self.ledger.approve('run','ready','sha','reviewer'); self.ledger.reserve_scoped('run','ready','sha','pr')
        self.ledger.resolve_scoped('run','ready','sha','pr','executed',followup='post_ready_review')
        self.assertEqual(self.ledger.pending_followups('run'),[('ready','sha','post_ready_review')])

    def test_pending_followup_blocks_further_mutation(self):
        self.ledger.approve('run','ready','sha','reviewer'); self.ledger.reserve_scoped('run','ready','sha','pr')
        self.ledger.resolve_scoped('run','ready','sha','pr','executed',followup='post_ready_review')
        with self.assertRaises(ActionConflict): self.ledger.approve('run','push','sha2','reviewer')

    def test_followup_cannot_be_cleared_for_another_target(self):
        self.ledger.approve('run','ready','sha','reviewer'); self.ledger.reserve_scoped('run','ready','sha','repo:pr:1')
        self.ledger.resolve_scoped('run','ready','sha','repo:pr:1','executed',followup='post_ready_review')
        with self.assertRaises(ActionConflict): self.ledger.complete_followup('run','sha','post_ready_review','repo:pr:2','proof',lambda run:True)
        self.assertTrue(self.ledger.pending_followups('run'))
