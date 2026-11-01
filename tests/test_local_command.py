import tempfile
import unittest
import os
from runner.local_command import LocalCommandBackend, LocalCommandError
from runner.local_scope import LocalScopes
from tests.test_local_scope import Backend

class Supervisor:
    def __init__(self,db,backend):self.db=db;self.backend=backend
    def preflight(self):return False
    def fence(self,run):return True

class LocalCommandTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_guarded_command_executes_after_scope_receipt(self):
        import time
        from pathlib import Path
        from types import SimpleNamespace
        from runner.local_command import _run_scoped
        from runner.local_scope import SystemdScopes
        with tempfile.TemporaryDirectory(prefix='workflow-command-') as root:
            backend=SystemdScopes(os.environ)
            supervisor=SimpleNamespace(db=str(Path(root)/'state.db'),backend=backend,environment=dict(os.environ),fence=lambda _:True)
            units=[]
            result=_run_scoped(supervisor,'run',root,['/usr/bin/printf','guarded'],{'PATH':'/usr/bin:/bin'},time.monotonic()+30,1024,units.append)
            self.assertEqual((result['exit_code'],result['stdout']),(0,b'guarded'))
            scopes=LocalScopes(supervisor.db,backend)
            try:
                self.assertEqual(scopes.state('run',units[0]),'terminated')
                self.assertEqual(scopes.db.execute('SELECT run,unit FROM local_scope_launch_receipts').fetchone(),('run',units[0]))
            finally:scopes.close()
    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_guarded_command_never_runs_when_receipt_binding_fails(self):
        import time
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from runner.local_command import _run_scoped
        from runner.local_scope import ScopeError, SystemdScopes
        with tempfile.TemporaryDirectory(prefix='workflow-command-failure-') as root:
            backend=SystemdScopes(os.environ)
            supervisor=SimpleNamespace(db=str(Path(root)/'state.db'),backend=backend,environment=dict(os.environ),fence=lambda _:True)
            units=[];groups=[];marker=Path(root)/'executed'
            def reject(scopes,run,unit,launch):
                groups.append(backend.inspect(unit)['cgroup'])
                raise ScopeError('audit unavailable')
            with patch.object(LocalScopes,'bind_confirmed',autospec=True,side_effect=reject):
                deadline=time.monotonic()+30
                with self.assertRaises(LocalCommandError):
                    _run_scoped(supervisor,'run',root,['/usr/bin/touch',str(marker)],{'PATH':'/usr/bin:/bin'},deadline,1024,units.append)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline and not backend.empty_cgroup(groups[0]):time.sleep(.05)
            self.assertTrue(backend.empty_cgroup(groups[0]));self.assertFalse(marker.exists())
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
            deadline=time.monotonic()+10
            supervisor=SimpleNamespace(db=str(Path(root)/'state.db'),backend=Backend(),environment={'HOME':root})
            from runner.engineering_workspace import EngineeringWorkspace
            (Path(root)/'workspaces').mkdir(mode=0o700)
            registry=EngineeringWorkspace(root);registry.close();LocalCommandBackend(supervisor)
            scopes=LocalScopes(supervisor.db,supervisor.backend);unit=scopes.reserve('run');scopes.close()
            with patch('runner.local_command.subprocess.Popen') as launch:
                with self.assertRaises(LocalCommandError):
                    _run_scoped(supervisor,'run',root,['/bin/true'],{},deadline,1024,lambda _:None,reserved_command=('job',unit))
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
            deadline=time.monotonic()+10
            supervisor=SimpleNamespace(db=str(Path(root)/'state.db'),backend=Backend(),environment={'HOME':root})
            registry=EngineeringWorkspace(root);LocalCommandBackend(supervisor)
            scopes=LocalScopes(supervisor.db,supervisor.backend);unit=scopes.reserve('run');scopes.close()
            registry.db.execute("INSERT INTO engineering_workspaces VALUES('run','o/r','base','branch',0,?,'policy','job')",(root,))
            registry.db.execute("INSERT INTO engineering_local_scopes VALUES('run','job',?)",(unit,))
            registry.db.execute("INSERT INTO local_command_scopes VALUES('job','run',?,NULL)",(unit,))
            try:
                with patch('runner.local_command.subprocess.Popen',side_effect=OSError('launch uncertain')) as launch:
                    trigger=registry.db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='local_command_launch_audit_insert'").fetchone()[0]
                    registry.db.execute('DROP TRIGGER local_command_launch_audit_insert')
                    with self.assertRaises(LocalCommandError): _run_scoped(supervisor,'run',root,['/bin/true'],{},deadline,1024,lambda _:None,reserved_command=('job',unit))
                    launch.assert_not_called()
                    registry.db.execute(trigger)
                    registry.db.execute("CREATE TRIGGER reject_launch_audit BEFORE INSERT ON local_command_launch_audit BEGIN SELECT RAISE(ABORT,'unavailable'); END")
                    with self.assertRaises(LocalCommandError): _run_scoped(supervisor,'run',root,['/bin/true'],{},deadline,1024,lambda _:None,reserved_command=('job',unit))
                    launch.assert_not_called()
                    self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM local_command_launches').fetchone(),(0,))
                    registry.db.execute('DROP TRIGGER reject_launch_audit')
                    gate=threading.Barrier(2)
                    def run():
                        gate.wait(timeout=5)
                        with self.assertRaises(LocalCommandError): _run_scoped(supervisor,'run',root,['/bin/true'],{},deadline,1024,lambda _:None,reserved_command=('job',unit))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        futures=[pool.submit(run) for _ in range(2)]
                        for future in futures: future.result(timeout=10)
                    self.assertEqual(launch.call_count,1)
                from runner.audit_export import read_events
                events,_=read_events(registry.db,'local_command_launch_audit',0,10,b'x'*32)
                self.assertEqual(len(events),1);self.assertEqual(events[0]['status'],'starting')
                self.assertNotIn(unit,str(events))
            finally: registry.close()

    def test_command_result_protocol_rejects_invalid_or_unfenced_evidence(self):
        from types import SimpleNamespace
        from runner.local_command import _read_command_result
        valid=[{'stdout':'b2s='},{'stderr':''},{'exit_code':0}]
        supervisor=SimpleNamespace(fence=lambda _:True)
        messages=iter(valid)
        self.assertEqual(_read_command_result(SimpleNamespace(receive=lambda:next(messages)),supervisor,'run',2),(0,b'ok',b''))
        for payload,limit,active in (([{},*valid[1:]],2,True),
                                     ([valid[0],{},valid[2]],2,True),
                                     ([*valid[:2],{'exit_code':True}],2,True),
                                     ([{'stdout':'@@'},*valid[1:]],2,True),
                                     (valid,1,True),(valid,2,False)):
            with self.subTest(payload=payload,limit=limit,active=active):
                messages=iter(payload)
                transport=SimpleNamespace(receive=lambda:next(messages))
                supervisor.fence=lambda _:active
                with self.assertRaises((LocalCommandError,ValueError)):
                    _read_command_result(transport,supervisor,'run',limit)

    def test_unverified_runtime_fails_before_command_launch(self):
        with tempfile.TemporaryDirectory() as root:
            backend=LocalCommandBackend(Supervisor(root+'/state.db',Backend()))
            with self.assertRaises(LocalCommandError): backend.execute(assignment='job',run='run',workspace=root,commit='x',argv=['/bin/true'],timeout=5,output_limit=100)
