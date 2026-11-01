import hashlib
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class LocalStopWitnessTests(unittest.TestCase):
    def test_normal_stop_requires_witness_completion(self):
        from runner.local_scope import LocalScopes,ScopeError
        from tests.test_local_scope import Backend
        for succeeds in (False,True):
            with self.subTest(succeeds=succeeds),tempfile.TemporaryDirectory() as root:
                backend=Backend();backend.no_pending_job=lambda _:True
                scopes=LocalScopes(str(Path(root)/'state.db'),backend);unit=scopes.reserve('run');backend.unit=unit;scopes.bind('run',unit)
                def witness(path,run,scope,nonce):
                    from contextlib import closing
                    with closing(sqlite3.connect(path,isolation_level=None)) as db:
                        db.execute("UPDATE local_scope_stop_operations SET status='active' WHERE unit=?",(scope,))
                        if succeeds:db.execute("UPDATE local_scope_stop_operations SET status='completed' WHERE unit=?",(scope,))
                    if succeeds:backend.state='inactive'
                    return succeeds
                backend.stop_witnessed=witness
                try:
                    if succeeds:
                        self.assertTrue(scopes.stop('run',unit));self.assertEqual(scopes.state('run',unit),'terminated')
                    else:
                        with self.assertRaises(ScopeError):scopes.stop('run',unit)
                        self.assertEqual(scopes.state('run',unit),'stopping')
                        with self.assertRaises(ScopeError):scopes.stop('run',unit)
                finally:scopes.close()

    def test_detached_launcher_passes_request_through_stdin_with_minimal_environment(self):
        import io,json
        from types import SimpleNamespace
        from runner.local_stop import start_stop_witness
        class Input(io.BytesIO):
            def close(self):self.saved=self.getvalue();super().close()
        stream=Input();process=SimpleNamespace(stdin=stream)
        with patch('runner.local_stop.subprocess.Popen',return_value=process) as launch:
            result=start_stop_witness('/private/state.db','run','unit','secret-nonce',{'HOME':'/private','TOKEN':'secret','DBUS_SESSION_BUS_ADDRESS':'unix:path=/private/bus'})
        self.assertIs(result,process)
        args=launch.call_args.args[0];options=launch.call_args.kwargs
        self.assertNotIn('secret-nonce',str(args));self.assertTrue(options['start_new_session'])
        self.assertEqual(options['env'],{'HOME':'/private','DBUS_SESSION_BUS_ADDRESS':'unix:path=/private/bus','PATH':'/usr/bin:/bin'})
        self.assertEqual(json.loads(stream.saved),{'path':'/private/state.db','run':'run','unit':'unit','nonce':'secret-nonce'})

    def test_stop_reservation_and_audit_are_atomic(self):
        from runner.local_scope import LocalScopes,ScopeError
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            backend=Backend();scopes=LocalScopes(str(Path(root)/'state.db'),backend)
            try:
                unit=scopes.reserve('run');backend.unit=unit;scopes.bind('run',unit)
                trigger=scopes.db.execute("SELECT sql FROM sqlite_master WHERE name='local_scope_stop_audit_insert'").fetchone()[0]
                scopes.db.execute('DROP TRIGGER local_scope_stop_audit_insert')
                with self.assertRaises(ScopeError):scopes.reserve_stop('run',unit)
                self.assertEqual(scopes.state('run',unit),'running')
                scopes.db.execute(trigger);nonce=scopes.reserve_stop('run',unit)
                self.assertEqual(len(nonce),64);self.assertEqual(scopes.state('run',unit),'stopping')
                row=scopes.db.execute('SELECT nonce_hash,status FROM local_scope_stop_operations').fetchone()
                self.assertEqual(row,(hashlib.sha256(bytes.fromhex(nonce)).hexdigest(),'pending'))
                self.assertEqual(scopes.db.execute('SELECT status FROM local_scope_stop_audit').fetchall(),[('pending',)])
                with self.assertRaises(ScopeError):scopes.reserve_stop('run',unit)
            finally:scopes.close()

    def fixture(self,root):
        path=str(Path(root)/'state.db');db=sqlite3.connect(path,isolation_level=None)
        db.executescript('''CREATE TABLE local_scopes(run TEXT,unit TEXT,invocation TEXT,cgroup TEXT,state TEXT);
            CREATE TABLE local_scope_stop_operations(run TEXT,unit TEXT,invocation TEXT,cgroup TEXT,nonce_hash TEXT,status TEXT);
            CREATE TABLE local_scope_stop_audit(id INTEGER PRIMARY KEY,schema_version INTEGER,run TEXT,unit TEXT,invocation TEXT,cgroup TEXT,nonce_hash TEXT,status TEXT);
            CREATE TRIGGER stop_insert AFTER INSERT ON local_scope_stop_operations BEGIN
                INSERT INTO local_scope_stop_audit(schema_version,run,unit,invocation,cgroup,nonce_hash,status)
                VALUES(1,NEW.run,NEW.unit,NEW.invocation,NEW.cgroup,NEW.nonce_hash,NEW.status); END;
            CREATE TRIGGER stop_update AFTER UPDATE OF status ON local_scope_stop_operations BEGIN
                INSERT INTO local_scope_stop_audit(schema_version,run,unit,invocation,cgroup,nonce_hash,status)
                VALUES(1,NEW.run,NEW.unit,NEW.invocation,NEW.cgroup,NEW.nonce_hash,NEW.status); END;''')
        unit='agent-workflows-'+'a'*48+'.scope';group='/user.slice/'+unit;nonce='b'*64
        db.execute('INSERT INTO local_scopes VALUES(?,?,?,?,?)',('run',unit,'c'*32,group,'stopping'))
        db.execute('INSERT INTO local_scope_stop_operations VALUES(?,?,?,?,?,?)',('run',unit,'c'*32,group,hashlib.sha256(bytes.fromhex(nonce)).hexdigest(),'pending'))
        self.addCleanup(db.close)
        return path,db,unit,group,nonce

    def test_successful_stop_writes_exact_completion_after_client_returns(self):
        from runner.local_stop_witness import run_stop
        with tempfile.TemporaryDirectory() as root:
            path,db,unit,group,nonce=self.fixture(root)
            def command(args,**kwargs):
                if 'show' in args:
                    return subprocess.CompletedProcess(args,0,f'Id={unit}\nLoadState=loaded\nActiveState=active\nInvocationID={"c"*32}\nControlGroup={group}\n')
                self.assertEqual(db.execute('SELECT status FROM local_scope_stop_operations').fetchone(),('active',))
                return subprocess.CompletedProcess(args,0,'')
            with patch('runner.local_stop_witness.subprocess.run',side_effect=command):
                self.assertTrue(run_stop(path,'run',unit,nonce))
            self.assertEqual(db.execute('SELECT status FROM local_scope_stop_operations').fetchone(),('completed',))
            self.assertEqual(db.execute('SELECT status FROM local_scope_stop_audit ORDER BY id').fetchall(),[('pending',),('active',),('completed',)])

    def test_failed_or_mismatched_stop_never_creates_completion(self):
        from runner.local_stop_witness import run_stop
        for failure in ('nonce','identity','command','audit'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as root:
                path,db,unit,group,nonce=self.fixture(root)
                if failure=='nonce':nonce='d'*64
                if failure=='audit':db.execute('DROP TRIGGER stop_update')
                def command(args,**kwargs):
                    if 'show' in args:
                        invocation='e'*32 if failure=='identity' else 'c'*32
                        return subprocess.CompletedProcess(args,0,f'Id={unit}\nLoadState=loaded\nActiveState=active\nInvocationID={invocation}\nControlGroup={group}\n')
                    raise subprocess.TimeoutExpired(args,1)
                with patch('runner.local_stop_witness.subprocess.run',side_effect=command):
                    self.assertFalse(run_stop(path,'run',unit,nonce))
                self.assertNotEqual(db.execute('SELECT status FROM local_scope_stop_operations').fetchone(),('completed',))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM local_scope_stop_audit WHERE status='completed'").fetchone(),(0,))

    def test_duplicate_witness_cannot_issue_a_second_stop(self):
        from runner.local_stop_witness import run_stop
        with tempfile.TemporaryDirectory() as root:
            path,db,unit,group,nonce=self.fixture(root)
            db.execute("UPDATE local_scope_stop_operations SET status='active'")
            with patch('runner.local_stop_witness.subprocess.run') as command:
                self.assertFalse(run_stop(path,'run',unit,nonce));command.assert_not_called()

    def test_missing_completion_audit_keeps_stop_unresolved(self):
        from runner.local_stop_witness import run_stop
        with tempfile.TemporaryDirectory() as root:
            path,db,unit,group,nonce=self.fixture(root)
            db.execute("CREATE TRIGGER reject_completion BEFORE INSERT ON local_scope_stop_audit WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'unavailable'); END")
            def command(args,**kwargs):
                if 'show' in args:return subprocess.CompletedProcess(args,0,f'Id={unit}\nLoadState=loaded\nActiveState=active\nInvocationID={"c"*32}\nControlGroup={group}\n')
                return subprocess.CompletedProcess(args,0,'')
            with patch('runner.local_stop_witness.subprocess.run',side_effect=command):
                self.assertFalse(run_stop(path,'run',unit,nonce))
            self.assertEqual(db.execute('SELECT status FROM local_scope_stop_operations').fetchone(),('active',))
            self.assertEqual(db.execute('SELECT status FROM local_scope_stop_audit').fetchall(),[('pending',),('active',)])

    def test_missing_reservation_audit_never_submits_stop(self):
        from runner.local_stop_witness import run_stop
        with tempfile.TemporaryDirectory() as root:
            path,db,unit,group,nonce=self.fixture(root)
            db.execute("DELETE FROM local_scope_stop_audit WHERE status='pending'")
            with patch('runner.local_stop_witness.subprocess.run') as command:
                self.assertFalse(run_stop(path,'run',unit,nonce));command.assert_not_called()
            self.assertEqual(db.execute('SELECT status FROM local_scope_stop_operations').fetchone(),('pending',))
