import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from runner.local_scope import LocalScopes
from runner.manual_lifecycle import ManualLifecycle
from runner.service import Service
from tests.test_local_scope import Backend


class CheckpointHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.service=Service(self.root);self.addCleanup(self.service.close)
        self.service.lifecycle=ManualLifecycle(str(self.root/'state.db'),'worker','inc');self.addCleanup(self.service.lifecycle.close)
        self.service.model_supervisor=SimpleNamespace(db=str(self.root/'state.db'))
        scopes=LocalScopes(str(self.root/'state.db'),Backend());scopes.close()
        self.service.graph.db.execute('CREATE TABLE local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
        self.service.create('run','marketing','task');self.service.enqueue('run')
        self.queue=self.service.lifecycle.claim()['token'];self.graph=self.service.claim('run')

    def test_clean_checkpoint_preserves_run_and_rejects_old_claims(self):
        from runner.checkpoint import checkpoint
        from runner.graph import GraphError
        result=checkpoint(self.service,'run',self.queue,self.graph)
        self.assertEqual(result,{'run':'run','state':'checkpointed'})
        db=self.service.graph.db
        self.assertEqual(db.execute("SELECT status FROM graph_runs WHERE id='run'").fetchone(),('active',))
        self.assertEqual(db.execute("SELECT status,token FROM jobs WHERE run='run'").fetchone(),('queued',None))
        self.assertFalse(self.service.queue.may_start('run',self.queue))
        with self.assertRaises(GraphError):self.service.graph._lease('run',self.graph)
        self.assertEqual(db.execute("SELECT state FROM manual_assignments WHERE run='run'").fetchone(),('checkpointed',))
        from runner.audit_export import read_events
        from runner.otel_audit import _attributes
        events,_=read_events(db,'checkpoint_audit',0,10,b'x'*32)
        self.assertEqual(events[0]['status'],'checkpointed');self.assertNotIn('run',events[0]['run_id'])
        self.assertEqual(_attributes(events[0])['status'],'checkpointed')
        from runner.manual_lifecycle import ManualLifecycleError
        with self.assertRaises(ManualLifecycleError): self.service.lifecycle.claim()
        self.assertEqual(self.service.lifecycle.register_checkpoint_replacement('run','inc','inc-next'),{'incarnation':'inc-next'})
        self.assertEqual(db.execute("SELECT run,target,old_incarnation,new_incarnation FROM checkpoint_replacements").fetchall(),[('run','worker','inc','inc-next')])
        with self.assertRaises(ManualLifecycleError): self.service.lifecycle.claim()
        lifecycle=ManualLifecycle(str(self.root/'state.db'),'worker','inc-next');self.addCleanup(lifecycle.close)
        replacement=lifecycle.claim();self.assertEqual(replacement['run'],'run')
        self.assertNotEqual(replacement['token'],self.queue)
        self.assertNotEqual(self.service.claim('run'),self.graph)

    def test_repeated_handoffs_preserve_identity_history_and_reject_reuse(self):
        from runner.checkpoint import checkpoint
        from runner.manual_lifecycle import ManualLifecycleError
        checkpoint(self.service,'run',self.queue,self.graph)
        self.service.lifecycle.register_checkpoint_replacement('run','inc','next')
        replacement=ManualLifecycle(str(self.root/'state.db'),'worker','next');self.addCleanup(replacement.close)
        queue=replacement.claim()['token'];claim=self.service.claim('run')
        self.service.lifecycle=replacement
        checkpoint(self.service,'run',queue,claim)
        with self.assertRaises(ManualLifecycleError):replacement.register_checkpoint_replacement('run','next','inc')
        replacement.register_checkpoint_replacement('run','next','third')
        rows=self.service.graph.db.execute('SELECT old_incarnation,new_incarnation FROM checkpoint_replacements ORDER BY id').fetchall()
        self.assertEqual(rows,[('inc','next'),('next','third')])
        third=ManualLifecycle(str(self.root/'state.db'),'worker','third');self.addCleanup(third.close)
        self.assertEqual(third.claim()['run'],'run')

    def test_replacement_control_is_owner_only(self):
        from runner.checkpoint import checkpoint
        from runner.control import ControlError,ControlPlane
        checkpoint(self.service,'run',self.queue,self.graph)
        control=ControlPlane(self.service,'secret')
        request={'action':'register_checkpoint_replacement','run':'run','old_incarnation':'inc','new_incarnation':'next'}
        with self.assertRaises(ControlError):control.handle('wrong',request)
        self.assertEqual(self.service.lifecycle.status('run')['incarnation'],'inc')
        self.assertEqual(control.handle('secret',request),{'incarnation':'next'})
        from runner.audit_export import read_events
        events,_=read_events(self.service.graph.db,'checkpoint_audit',0,10,b'x'*32)
        self.assertEqual([event['status'] for event in events],['checkpointed','replacement_registered'])
        self.assertNotIn('next',str(events))

    def test_replacement_revalidates_quiescence_and_rejects_reused_identity(self):
        from runner.checkpoint import CheckpointError,checkpoint
        from runner.manual_lifecycle import ManualLifecycleError
        checkpoint(self.service,'run',self.queue,self.graph)
        lifecycle=self.service.lifecycle;db=self.service.graph.db
        for old,new in (('wrong','next'),('inc','inc')):
            with self.assertRaises(ManualLifecycleError): lifecycle.register_checkpoint_replacement('run',old,new)
        db.execute("INSERT INTO manual_incarnation_history VALUES('worker','used')")
        with self.assertRaises(ManualLifecycleError): lifecycle.register_checkpoint_replacement('run','inc','used')
        self.service.actions.approve('run','push:test','version','owner')
        with self.assertRaises(CheckpointError): lifecycle.register_checkpoint_replacement('run','inc','next')
        self.assertEqual(db.execute("SELECT incarnation,state FROM manual_workers WHERE target='worker'").fetchone(),('inc','checkpointed'))
        self.assertFalse(db.execute("SELECT 1 FROM manual_incarnation_history WHERE incarnation='next'").fetchone())

    def test_concurrent_replacements_have_exactly_one_winner(self):
        import threading
        from runner.checkpoint import checkpoint
        checkpoint(self.service,'run',self.queue,self.graph)
        barrier=threading.Barrier(2);results=[]
        def register(new):
            lifecycle=ManualLifecycle(str(self.root/'state.db'),'worker','inc')
            try:
                barrier.wait(timeout=5)
                try:lifecycle.register_checkpoint_replacement('run','inc',new);results.append('registered')
                except Exception:results.append('rejected')
            finally:lifecycle.close()
        threads=[threading.Thread(target=register,args=(new,)) for new in ('next-one','next-two')]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(results,['registered','rejected'])
        self.assertEqual(self.service.graph.db.execute("SELECT COUNT(*) FROM checkpoint_audit WHERE status='replacement_registered'").fetchone(),(1,))

    def test_replacement_audit_failure_rolls_back_identity(self):
        import sqlite3
        from runner.checkpoint import checkpoint
        checkpoint(self.service,'run',self.queue,self.graph)
        db=self.service.graph.db
        db.execute("CREATE TRIGGER reject_replacement BEFORE INSERT ON checkpoint_audit BEGIN SELECT RAISE(ABORT,'unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.service.lifecycle.register_checkpoint_replacement('run','inc','next')
        self.assertEqual(db.execute("SELECT incarnation,state FROM manual_workers WHERE target='worker'").fetchone(),('inc','checkpointed'))
        self.assertEqual(db.execute("SELECT incarnation,state FROM manual_assignments WHERE run='run'").fetchone(),('inc','checkpointed'))
        self.assertFalse(db.execute("SELECT 1 FROM manual_incarnation_history WHERE incarnation='next'").fetchone())
        self.assertEqual(db.execute('SELECT COUNT(*) FROM checkpoint_replacements').fetchone(),(0,))

    def test_replacement_history_failure_rolls_back_handoff(self):
        import sqlite3
        from runner.checkpoint import checkpoint
        checkpoint(self.service,'run',self.queue,self.graph)
        db=self.service.graph.db
        db.execute("CREATE TRIGGER reject_history BEFORE INSERT ON checkpoint_replacements BEGIN SELECT RAISE(ABORT,'unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.service.lifecycle.register_checkpoint_replacement('run','inc','next')
        self.assertEqual(db.execute("SELECT incarnation,state FROM manual_workers WHERE target='worker'").fetchone(),('inc','checkpointed'))
        self.assertEqual(db.execute("SELECT incarnation,state FROM manual_assignments WHERE run='run'").fetchone(),('inc','checkpointed'))
        self.assertFalse(db.execute("SELECT 1 FROM manual_incarnation_history WHERE incarnation='next'").fetchone())
        self.assertEqual(db.execute("SELECT COUNT(*) FROM checkpoint_audit WHERE status='replacement_registered'").fetchone(),(0,))

    def test_unresolved_scope_blocks_checkpoint_without_releasing_claims(self):
        from runner.checkpoint import CheckpointError,checkpoint
        scopes=LocalScopes(str(self.root/'state.db'),Backend());self.addCleanup(scopes.close)
        scopes.reserve('run')
        with self.assertRaises(CheckpointError):checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue))
        self.service.graph._lease('run',self.graph)
        self.assertEqual(self.service.lifecycle.status('run')['status'],'running')

    def test_uncertain_remote_action_blocks_checkpoint(self):
        from runner.checkpoint import CheckpointError,checkpoint
        self.service.actions.approve('run','push:test','version','owner')
        self.service.actions.reserve('run','push:test','version')
        with self.assertRaises(CheckpointError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue))
        self.assertEqual(self.service.actions.db.execute("SELECT status FROM external_actions WHERE run='run'").fetchone(),('uncertain',))

    def test_unknown_session_state_blocks_checkpoint(self):
        from runner.checkpoint import CheckpointError,checkpoint
        db=self.service.graph.db
        db.execute("INSERT INTO local_sessions VALUES('run','implement','session','binding',NULL)")
        with self.assertRaises(CheckpointError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue))

    def test_checkpoint_audit_failure_rolls_back_every_claim(self):
        import sqlite3
        from runner.checkpoint import checkpoint
        db=self.service.graph.db
        db.execute('CREATE TABLE checkpoint_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
        db.execute("CREATE TRIGGER reject_checkpoint_audit BEFORE INSERT ON checkpoint_audit BEGIN SELECT RAISE(ABORT,'unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue));self.service.graph._lease('run',self.graph)
        self.assertEqual(self.service.lifecycle.status('run')['status'],'running')

    def test_missing_scope_table_blocks_checkpoint(self):
        from runner.checkpoint import CheckpointError,checkpoint
        self.service.graph.db.execute('DROP TABLE local_scopes')
        with self.assertRaises(CheckpointError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue))

    def test_missing_job_audit_trigger_blocks_checkpoint(self):
        from runner.checkpoint import CheckpointError,checkpoint
        self.service.graph.db.execute('DROP TRIGGER job_audit_update')
        with self.assertRaises(CheckpointError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue));self.service.graph._lease('run',self.graph)
        self.assertEqual(self.service.lifecycle.status('run')['status'],'running')

    def test_missing_manual_audit_trigger_blocks_checkpoint(self):
        from runner.checkpoint import CheckpointError,checkpoint
        self.service.graph.db.execute('DROP TRIGGER manual_lifecycle_audit_update')
        with self.assertRaises(CheckpointError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue));self.service.graph._lease('run',self.graph)
        self.assertEqual(self.service.lifecycle.status('run')['status'],'running')

    def test_unconsumed_approval_blocks_checkpoint(self):
        from runner.checkpoint import CheckpointError,checkpoint
        self.service.actions.approve('run','push:test','version','owner')
        with self.assertRaises(CheckpointError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue))
        self.assertEqual(self.service.actions.db.execute("SELECT status FROM external_actions WHERE run='run'").fetchone(),('approved',))

    def test_malformed_terminal_scope_blocks_checkpoint(self):
        from runner.checkpoint import CheckpointError,checkpoint
        scopes=LocalScopes(str(self.root/'state.db'),Backend());self.addCleanup(scopes.close)
        unit=scopes.reserve('run')
        scopes.db.execute("UPDATE local_scopes SET state='terminated',invocation='bad',cgroup='/wrong' WHERE unit=?",(unit,))
        with self.assertRaises(CheckpointError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue))

    def test_foreign_supervisor_database_blocks_checkpoint(self):
        from runner.checkpoint import CheckpointError,checkpoint
        self.service.model_supervisor.db=str(self.root/'other.db')
        with self.assertRaises(CheckpointError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue))

    def test_unknown_attempt_outcome_blocks_checkpoint(self):
        from runner.checkpoint import CheckpointError,checkpoint
        self.service.graph.db.execute("INSERT INTO graph_attempts VALUES('run','brief','version',1,'unknown')")
        with self.assertRaises(CheckpointError): checkpoint(self.service,'run',self.queue,self.graph)
        self.assertTrue(self.service.queue.may_start('run',self.queue))
