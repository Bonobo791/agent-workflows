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
            self.assertFalse(backend.verify_terminal('job','run',1));self.assertFalse(backend.verify_terminal('job','other',0));self.assertFalse(backend.verify_terminal('other','run',0))
            scopes.close()
    def test_unverified_runtime_fails_before_command_launch(self):
        with tempfile.TemporaryDirectory() as root:
            backend=LocalCommandBackend(Supervisor(root+'/state.db',Backend()))
            with self.assertRaises(LocalCommandError): backend.execute(assignment='job',run='run',workspace=root,commit='x',argv=['/bin/true'],timeout=5,output_limit=100)
