import tempfile
import unittest
from runner.local_command import LocalCommandBackend, LocalCommandError
from runner.local_scope import LocalScopes
from tests.test_local_scope import Backend

class Supervisor:
    def __init__(self,db,backend):self.db=db;self.backend=backend
    def preflight(self):return False
    def fence(self,run):return True

class LocalCommandTests(unittest.TestCase):
    def test_terminal_receipt_requires_exact_scope_and_exit_code(self):
        with tempfile.TemporaryDirectory() as root:
            db=root+'/state.db';engine=Backend();supervisor=Supervisor(db,engine);scopes=LocalScopes(db,engine);unit=scopes.reserve('run');engine.unit=unit;scopes.bind('run',unit)
            backend=LocalCommandBackend(supervisor);connection=backend._db()
            try:connection.execute('INSERT INTO local_command_scopes VALUES(?,?,?,?)',('job','run',unit,0))
            finally:connection.close()
            self.assertFalse(backend.verify_terminal('job','run',0))
            self.assertTrue(scopes.stop('run',unit));self.assertTrue(backend.verify_terminal('job','run',0))
            scopes.db.execute('UPDATE local_scopes SET invocation=NULL WHERE unit=?',(unit,))
            self.assertFalse(backend.verify_terminal('job','run',0))
            self.assertFalse(backend.verify_terminal('job','run',1));self.assertFalse(backend.verify_terminal('job','other',0));self.assertFalse(backend.verify_terminal('other','run',0))
            scopes.close()
    def test_unbound_reserved_command_cannot_launch(self):
        import time
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from runner.local_command import _run_scoped
        with tempfile.TemporaryDirectory() as root:
            supervisor=SimpleNamespace(db=str(Path(root)/'state.db'),backend=Backend(),environment={'HOME':root})
            from runner.engineering_workspace import EngineeringWorkspace
            (Path(root)/'workspaces').mkdir(mode=0o700)
            registry=EngineeringWorkspace(root);registry.close();LocalCommandBackend(supervisor)
            scopes=LocalScopes(supervisor.db,supervisor.backend);unit=scopes.reserve('run');scopes.close()
            with patch('runner.local_command.subprocess.Popen') as launch:
                with self.assertRaises(LocalCommandError):
                    _run_scoped(supervisor,'run',root,['/bin/true'],{},time.monotonic()+10,1024,lambda _:None,reserved_command=('job',unit))
                launch.assert_not_called()

    def test_reserved_command_cannot_be_launched_twice_after_uncertain_start(self):
        import concurrent.futures
        import threading
        import time
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from runner.engineering_workspace import EngineeringWorkspace
        from runner.local_command import _run_scoped
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'workspaces').mkdir(mode=0o700)
            supervisor=SimpleNamespace(db=str(Path(root)/'state.db'),backend=Backend(),environment={'HOME':root})
            registry=EngineeringWorkspace(root);LocalCommandBackend(supervisor)
            scopes=LocalScopes(supervisor.db,supervisor.backend);unit=scopes.reserve('run');scopes.close()
            registry.db.execute("INSERT INTO engineering_workspaces VALUES('run','o/r','base','branch',0,?,'policy','job')",(root,))
            registry.db.execute("INSERT INTO engineering_local_scopes VALUES('run','job',?)",(unit,))
            registry.db.execute("INSERT INTO local_command_scopes VALUES('job','run',?,NULL)",(unit,))
            try:
                with patch('runner.local_command.subprocess.Popen',side_effect=OSError('launch uncertain')) as launch:
                    registry.db.execute("CREATE TRIGGER reject_launch_audit BEFORE INSERT ON local_command_launch_audit BEGIN SELECT RAISE(ABORT,'unavailable'); END")
                    with self.assertRaises(LocalCommandError): _run_scoped(supervisor,'run',root,['/bin/true'],{},time.monotonic()+10,1024,lambda _:None,reserved_command=('job',unit))
                    launch.assert_not_called()
                    self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM local_command_launches').fetchone(),(0,))
                    registry.db.execute('DROP TRIGGER reject_launch_audit')
                    gate=threading.Barrier(2)
                    def run():
                        gate.wait(timeout=5)
                        with self.assertRaises(LocalCommandError): _run_scoped(supervisor,'run',root,['/bin/true'],{},time.monotonic()+10,1024,lambda _:None,reserved_command=('job',unit))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        futures=[pool.submit(run) for _ in range(2)]
                        for future in futures: future.result(timeout=10)
                    self.assertEqual(launch.call_count,1)
                from runner.audit_export import read_events
                events,_=read_events(registry.db,'local_command_launch_audit',0,10,b'x'*32)
                self.assertEqual(len(events),1);self.assertEqual(events[0]['status'],'starting')
                self.assertNotIn(unit,str(events))
            finally: registry.close()

    def test_unverified_runtime_fails_before_command_launch(self):
        with tempfile.TemporaryDirectory() as root:
            backend=LocalCommandBackend(Supervisor(root+'/state.db',Backend()))
            with self.assertRaises(LocalCommandError): backend.execute(assignment='job',run='run',workspace=root,commit='x',argv=['/bin/true'],timeout=5,output_limit=100)
