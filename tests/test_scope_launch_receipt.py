import os
import json
import time
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from runner.local_launch import GuardedLaunch
from runner.local_scope import LocalScopes, ScopeError
from tests.test_local_scope import Backend


class ReceiptBackend(Backend):
    def __init__(self):
        super().__init__();self.membership=True;self.pending_job=False
    def pid_in_cgroup(self,pid,cgroup):
        return self.membership and pid==123 and cgroup=='/user.slice/'+self.unit
    def no_pending_job(self,unit): return not self.pending_job


class ScopeLaunchReceiptTests(unittest.TestCase):
    def fixture(self,root):
        backend=ReceiptBackend();scopes=LocalScopes(str(Path(root)/'state.db'),backend)
        unit=scopes.reserve('run');backend.unit=unit
        read_fd,write_fd=os.pipe();ack_read,ack_write=os.pipe()
        launch=GuardedLaunch(SimpleNamespace(pid=123,poll=lambda:None),ack_read,write_fd,'a'*64)
        self.go_read=read_fd
        os.write(ack_write,json.dumps({'version':1,'nonce':'a'*64,'pid':123}).encode());os.close(ack_write)
        launch.receive(time.monotonic()+2,lambda:False)
        self.addCleanup(os.close,read_fd);self.addCleanup(launch.close);self.addCleanup(scopes.close)
        return backend,scopes,unit,launch

    def test_confirmed_binding_records_identity_and_audit_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            backend,scopes,unit,launch=self.fixture(root)
            scopes.bind_confirmed('run',unit,launch)
            row=scopes.db.execute('SELECT run,unit,invocation,cgroup,pid FROM local_scope_launch_receipts').fetchone()
            self.assertEqual(row,('run',unit,'a'*32,'/user.slice/'+unit,123))
            self.assertEqual(scopes.state('run',unit),'running')
            self.assertEqual(scopes.db.execute('SELECT run,unit,status FROM local_scope_launch_audit').fetchone(),('run',unit,'bound'))
            from runner.audit_export import read_events
            events,_=read_events(scopes.db,'local_scope_launch_audit',0,10,b'x'*32)
            self.assertEqual(len(events),1);self.assertEqual(events[0]['status'],'bound')
            self.assertNotIn(unit,str(events));self.assertNotIn('/user.slice/',str(events))

    def test_missing_acknowledgement_or_membership_never_binds(self):
        with tempfile.TemporaryDirectory() as root:
            backend,scopes,unit,launch=self.fixture(root)
            launch.nonce='b'*64
            with self.assertRaises(ScopeError):scopes.bind_confirmed('run',unit,launch)
            launch.nonce='a'*64;backend.membership=False
            with self.assertRaises(ScopeError):scopes.bind_confirmed('run',unit,launch)
            self.assertEqual(scopes.state('run',unit),'starting')
            self.assertEqual(scopes.db.execute('SELECT COUNT(*) FROM local_scope_launch_receipts').fetchone(),(0,))

    def test_missing_launch_audit_rolls_back_binding(self):
        with tempfile.TemporaryDirectory() as root:
            backend,scopes,unit,launch=self.fixture(root)
            scopes.db.execute('DROP TRIGGER local_scope_launch_receipt_audit_insert')
            with self.assertRaises(ScopeError):scopes.bind_confirmed('run',unit,launch)
            self.assertEqual(scopes.state('run',unit),'starting')
            self.assertEqual(scopes.db.execute('SELECT COUNT(*) FROM local_scope_launch_receipts').fetchone(),(0,))

    def test_release_holds_write_lock_while_checking_fence(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            backend,scopes,unit,launch=self.fixture(root);scopes.bind_confirmed('run',unit,launch)
            other=sqlite3.connect(str(Path(root)/'state.db'),timeout=.01,isolation_level=None)
            def fence():
                with self.assertRaises(sqlite3.OperationalError):other.execute("UPDATE local_scopes SET state='stopping' WHERE unit=?",(unit,))
                return True
            try:
                scopes.release_confirmed('run',unit,launch,fence)
                self.assertEqual(os.read(self.go_read,1),b'G')
            finally:other.close()

    def test_release_commit_failure_preserves_durable_launch_evidence(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            backend,scopes,unit,launch=self.fixture(root);scopes.bind_confirmed('run',unit,launch)
            db=scopes.db
            class FailCommit:
                def execute(self,sql,*args):
                    if sql=='COMMIT':raise sqlite3.OperationalError('commit unavailable')
                    return db.execute(sql,*args)
                def close(self):db.close()
            scopes.db=FailCommit()
            with self.assertRaises(sqlite3.OperationalError):scopes.release_confirmed('run',unit,launch,lambda:True)
            self.assertEqual(os.read(self.go_read,1),b'G')
            self.assertEqual(scopes.state('run',unit),'running')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM local_scope_launch_receipts').fetchone(),(1,))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM local_scope_launch_audit').fetchone(),(1,))

    def test_release_rejects_changed_scope_or_fence(self):
        for change in ('fence','scope','receipt','audit'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                backend,scopes,unit,launch=self.fixture(root);scopes.bind_confirmed('run',unit,launch)
                if change=='scope':scopes.db.execute("UPDATE local_scopes SET state='stopping' WHERE unit=?",(unit,))
                if change=='receipt':scopes.db.execute("UPDATE local_scope_launch_receipts SET nonce_hash=? WHERE unit=?",('b'*64,unit))
                if change=='audit':scopes.db.execute('DELETE FROM local_scope_launch_audit WHERE unit=?',(unit,))
                with self.assertRaises(ScopeError):scopes.release_confirmed('run',unit,launch,lambda:change!='fence')
                launch.close();self.assertEqual(os.read(self.go_read,1),b'')

    def recovery_fixture(self,root):
        backend,scopes,unit,launch=self.fixture(root)
        scopes.bind_confirmed('run',unit,launch);backend.state='inactive'
        scopes.db.executescript("""CREATE TABLE manual_assignments(run TEXT,target TEXT,incarnation TEXT,state TEXT);
            CREATE TABLE manual_workers(target TEXT,incarnation TEXT,state TEXT);
            CREATE TABLE jobs(run TEXT,status TEXT);
            CREATE TABLE graph_runs(id TEXT,status TEXT);
            CREATE TABLE graph_claims(run TEXT);
            INSERT INTO manual_assignments VALUES('run','target','inc','pending');
            INSERT INTO manual_workers VALUES('target','inc','assigned');
            INSERT INTO jobs VALUES('run','uncertain');
            INSERT INTO graph_runs VALUES('run','cancelled');""")
        return backend,scopes,unit

    def test_inactive_confirmed_scope_requires_exact_fence_and_audit(self):
        with tempfile.TemporaryDirectory() as root:
            backend,scopes,unit=self.recovery_fixture(root)
            self.assertTrue(scopes.reconcile_confirmed('run',unit,('target','inc')))
            self.assertEqual(scopes.state('run',unit),'terminated')
            self.assertEqual(scopes.db.execute('SELECT run,unit,status FROM local_scope_recovery_audit').fetchone(),('run',unit,'reconciled'))

    def test_reconciliation_audit_failure_rolls_back_termination(self):
        import sqlite3
        for action,error in (("SELECT RAISE(ABORT,'unavailable')",sqlite3.IntegrityError),('SELECT RAISE(IGNORE)',ScopeError)):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as root:
                backend,scopes,unit=self.recovery_fixture(root)
                scopes.db.execute('CREATE TRIGGER reject_recovery BEFORE INSERT ON local_scope_recovery_audit BEGIN '+action+'; END')
                with self.assertRaises(error):scopes.reconcile_confirmed('run',unit,('target','inc'))
                self.assertEqual(scopes.state('run',unit),'running')
        with tempfile.TemporaryDirectory() as root:
            backend,scopes,unit=self.recovery_fixture(root)
            scopes.db.execute('DROP TRIGGER local_scope_audit_update')
            with self.assertRaises(ScopeError):scopes.reconcile_confirmed('run',unit,('target','inc'))
            self.assertEqual(scopes.state('run',unit),'running')
            self.assertEqual(scopes.db.execute('SELECT COUNT(*) FROM local_scope_recovery_audit').fetchone(),(0,))

    def test_scope_that_changes_during_reconciliation_stays_fenced(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            backend,scopes,unit=self.recovery_fixture(root)
            original=backend.inspect;calls=[]
            def inspect(value):
                calls.append(value)
                if len(calls)==2:backend.state='active'
                return original(value)
            with patch.object(backend,'inspect',side_effect=inspect):
                with self.assertRaises(ScopeError):scopes.reconcile_confirmed('run',unit,('target','inc'))
            self.assertEqual(scopes.state('run',unit),'running')
            self.assertEqual(scopes.db.execute('SELECT COUNT(*) FROM local_scope_recovery_audit').fetchone(),(0,))

    def test_reconciliation_rejects_changed_observed_identity(self):
        from unittest.mock import patch
        for key,value in (('cgroup','/other.scope'),('invocation','b'*32)):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as root:
                backend,scopes,unit=self.recovery_fixture(root)
                observed={**backend.inspect(unit),key:value}
                with patch.object(backend,'inspect',return_value=observed):
                    with self.assertRaises(ScopeError):scopes.reconcile_confirmed('run',unit,('target','inc'))
                self.assertEqual(scopes.state('run',unit),'running')

    def test_fence_cannot_change_during_reconciliation(self):
        import sqlite3
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            backend,scopes,unit=self.recovery_fixture(root)
            other=sqlite3.connect(str(Path(root)/'state.db'),timeout=.01,isolation_level=None)
            original=backend.inspect;attempts=[]
            def inspect(value):
                with self.assertRaises(sqlite3.OperationalError):
                    other.execute("UPDATE manual_assignments SET state='running' WHERE run='run'")
                attempts.append(value);return original(value)
            try:
                with patch.object(backend,'inspect',side_effect=inspect):
                    self.assertTrue(scopes.reconcile_confirmed('run',unit,('target','inc')))
                self.assertEqual(len(attempts),2)
                self.assertEqual(scopes.db.execute("SELECT state FROM manual_assignments WHERE run='run'").fetchone(),('pending',))
            finally:other.close()

    def test_inactive_scope_reconciliation_rejects_ambiguous_evidence(self):
        mutations=("DELETE FROM local_scope_launch_receipts", "DELETE FROM local_scope_launch_audit",
                   "UPDATE local_scope_launch_receipts SET cgroup='/wrong'", "UPDATE local_scopes SET state='stopping'",
                   "UPDATE manual_assignments SET state='running'", "UPDATE jobs SET status='running'",
                   "UPDATE graph_runs SET status='active'", "INSERT INTO graph_claims VALUES('run')")
        for mutation in mutations:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as root:
                backend,scopes,unit=self.recovery_fixture(root);scopes.db.execute(mutation)
                with self.assertRaises(ScopeError):scopes.reconcile_confirmed('run',unit,('target','inc'))
        for failure in ('active','nonempty','pending'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as root:
                backend,scopes,unit=self.recovery_fixture(root)
                if failure=='active':backend.state='active'
                if failure=='nonempty':backend.empty=False
                if failure=='pending':backend.pending_job=True
                with self.assertRaises(ScopeError):scopes.reconcile_confirmed('run',unit,('target','inc'))

    def test_owner_control_requires_fenced_run_and_does_not_confirm_worker(self):
        from runner.actions import ActionConflict
        from runner.control import ControlError,ControlPlane
        from runner.manual_lifecycle import ManualLifecycle
        from runner.service import Service
        with tempfile.TemporaryDirectory() as root:
            service=Service(root);lifecycle=ManualLifecycle(str(Path(root)/'state.db'),'target','inc')
            service.lifecycle=lifecycle
            try:
                service.create('run','marketing','task');service.enqueue('run');lifecycle.claim()
                backend,scopes,unit,launch=self.fixture(root);scopes.bind_confirmed('run',unit,launch);backend.state='inactive'
                service.model_supervisor=SimpleNamespace(backend=backend)
                service.actions.db.execute("INSERT INTO external_actions VALUES('run','remote_push','v1','owner','uncertain',1)")
                control=ControlPlane(service,'secret');request={'action':'reconcile_local_scope','run':'run','unit':unit}
                with self.assertRaises(ControlError):control.handle('wrong',request)
                with self.assertRaises(ControlError):control.handle('secret',request)
                service.request_lifecycle_stop('run')
                self.assertEqual(control.handle('secret',request),{'unit':unit,'state':'terminated'})
                self.assertEqual(lifecycle.status('run')['status'],'pending')
                self.assertEqual(service.graph.db.execute("SELECT status FROM graph_runs WHERE id='run'").fetchone(),('cancelled',))
                self.assertEqual(service.actions.db.execute("SELECT status FROM external_actions WHERE run='run' AND action='remote_push'").fetchone(),('uncertain',))
                with self.assertRaises(ActionConflict):service.actions.approve('run','remote_push','v2','owner')
                with self.assertRaises(ControlError):control.handle('secret',request)
            finally:lifecycle.close();service.close()
