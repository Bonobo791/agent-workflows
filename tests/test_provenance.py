import tempfile
import unittest
from runner.graph import GraphStore, marketing_definition
from runner.provenance import Provenance, ProvenanceError

class ProvenanceTests(unittest.TestCase):
    def test_execution_history_survives_restart_and_audit_failure_fails_closed(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            path=root+'/db'; graph=GraphStore(path); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            provenance=Provenance(graph); provenance.bind('run','canonical_draft','session',{'writer':'digest'},token)
            provenance.mark_completed('run','canonical_draft','session',token,'result-hash'); graph.close()
            graph=GraphStore(path); graph.register(marketing_definition()); provenance=Provenance(graph)
            rows=graph.db.execute('SELECT role,session,status,skills,output_hash FROM execution_audit ORDER BY id').fetchall()
            self.assertEqual(rows,[('canonical_draft','session','bound','{"writer":"digest"}',None),('canonical_draft','session','completed',None,'result-hash')])
            graph.db.execute("CREATE TRIGGER reject_execution_audit BEFORE INSERT ON execution_audit BEGIN SELECT RAISE(ABORT,'failure'); END")
            with self.assertRaises(sqlite3.IntegrityError): provenance.bind('run','editorial_review','review',{},token)
            self.assertFalse(provenance.verify('run','editorial_review','review',{}))
            graph.close()

    def test_trusted_binding_prevents_reviewer_reuse_and_detects_skill_mismatch(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            provenance=Provenance(graph)
            provenance.bind('run','canonical_draft','writer',{'editor':'a'},token)
            provenance.bind('run','editorial_review','reviewer',{'editor':'a'},token)
            with self.assertRaises(ProvenanceError): provenance.bind('run','seo_review','reviewer',{'editor':'a'},token)
            self.assertTrue(provenance.verify('run','editorial_review','reviewer',{'editor':'a'}))
            self.assertFalse(provenance.verify('run','editorial_review','reviewer',{'editor':'b'}))
            graph.close()

    def test_review_evidence_requires_registered_execution(self):
        from runner.provenance import validate_review
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            provenance=Provenance(graph); provenance.bind('run','editorial_review','actual',{'editor':'a'},token)
            self.assertFalse(validate_review(provenance,'run','editorial_review',{'session_id':'invented'},{'editor':'a'}))
            self.assertTrue(validate_review(provenance,'run','editorial_review',{'session_id':'actual'},{'editor':'a'}))
            graph.close()

    def test_session_cannot_be_reused_across_runs(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('a','marketing'); graph.create('b','marketing')
            first=graph.claim('a',60); second=graph.claim('b',60); provenance=Provenance(graph)
            provenance.bind('a','implement','same-thread',{},first)
            with self.assertRaises(ProvenanceError): provenance.bind('b','implement','same-thread',{},second)
            graph.close()

    def test_started_session_is_not_completed_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60)
            provenance=Provenance(graph); provenance.bind('r','review','session',{},token)
            self.assertFalse(provenance.completed('r','review','session'))
            provenance.mark_completed('r','review','session',token)
            self.assertTrue(provenance.completed('r','review','session')); graph.close()
