import sqlite3
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
    def test_action_audit_persists_and_failure_prevents_reservation(self):
        self.ledger.approve('run','push','sha','human')
        self.ledger.reserve('run','push','sha')
        self.ledger.resolve('run','push','sha','not_executed')
        self.ledger.approve('run','push','sha','human')
        rows=self.ledger.db.execute('SELECT status FROM action_audit ORDER BY id').fetchall()
        self.assertEqual(rows,[('approved',),('uncertain',),('not_executed',),('approved',)])
        self.ledger.db.execute("CREATE TRIGGER reject_action_audit BEFORE INSERT ON action_audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.ledger.reserve('run','push','sha')
        self.assertEqual(self.ledger.db.execute('SELECT status FROM external_actions').fetchone(),('approved',))
    def test_action_history_records_updates_from_another_connection(self):
        self.ledger.approve('run','freeze','sha','human')
        other=sqlite3.connect(self.tmp.name+'/state.db')
        try:
            with other:
                other.execute("UPDATE external_actions SET status='uncertain',updated=2 WHERE run='run'")
        finally: other.close()
        self.assertEqual(self.ledger.db.execute('SELECT status FROM action_audit ORDER BY id').fetchall(),[('approved',),('uncertain',)])
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
