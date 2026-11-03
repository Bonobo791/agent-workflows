import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runner.local_supervisor import LocalSupervisor, LocalSupervisorError
from tests.test_local_scope import Backend

class Handle:
    def __init__(self): self.unit='test-unit';self.output_dir='';self.output_path_prefix='';self.finished=False
    def finish(self): self.finished=True;return True
    def cancel(self): return self.finish()

@patch('runner.local_supervisor.scope_preflight',return_value=True)
@patch('runner.local_supervisor.sandbox_preflight',return_value=True)
class SupervisorTests(unittest.TestCase):
    def test_null_scope_state_blocks_session_resume(self,_sandbox,_scope):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            work=Path(root)/'work';work.mkdir(mode=0o700);binary=Path(root)/'codex';binary.write_text('x');binary.chmod(0o700)
            db=root+'/db';connection=sqlite3.connect(db)
            connection.execute('CREATE TABLE local_scopes(run TEXT,unit TEXT PRIMARY KEY,invocation TEXT,cgroup TEXT,state TEXT)')
            connection.execute("INSERT INTO local_scopes(run,unit,state) VALUES('r','unknown.scope',NULL)")
            connection.commit();connection.close()
            supervisor=LocalSupervisor(db,str(binary),{'HOME':root},Backend(),lambda run:True,lambda run,role:str(work),lambda:True,{'mcp_servers':[],'plugins':[]})
            _,binding=supervisor._binding('r','implement',{},'model')
            connection=supervisor._db()
            try:connection.execute("INSERT INTO local_sessions VALUES('r','implement','thread',?,'idle')",(binding,))
            finally:connection.close()
            self.assertFalse(supervisor.can_resume('r','implement','thread',{},'model'))
            from runner.local_scope import LocalScopes, ScopeError
            scopes=LocalScopes(db,Backend())
            try:
                with self.assertRaises(ScopeError): scopes.reserve('r')
            finally:scopes.close()

    def test_resume_requires_matching_role_model_skills_and_stopped_scope(self,_sandbox,_scope):
        with tempfile.TemporaryDirectory() as root:
            work=Path(root)/'work';work.mkdir(mode=0o700);binary=Path(root)/'codex';binary.write_text('x');binary.chmod(0o700)
            supervisor=LocalSupervisor(root+'/db',str(binary),{'HOME':root},Backend(),lambda run:True,lambda run,role:str(work),lambda:True,{'mcp_servers':[],'plugins':[]})
            with patch('runner.local_supervisor.start_local_process',return_value=Handle()):
                handle=supervisor.start_execution('r','implement',None,{'a':'hash'},'model')
                handle.bind_session('thread-1')
                self.assertFalse(supervisor.can_resume('r','implement','thread-1',{'a':'hash'},'model'))
                self.assertTrue(handle.finish())
                self.assertTrue(supervisor.can_resume('r','implement','thread-1',{'a':'hash'},'model'))
                self.assertFalse(supervisor.can_resume('r','implement','thread-1',{'a':'changed'},'model'))
                self.assertFalse(supervisor.can_resume('r','qa','thread-1',{'a':'hash'},'model'))
                with self.assertRaises(LocalSupervisorError): supervisor.start_execution('r','implement',None,{'a':'hash'},'model')
                resumed=supervisor.start_execution('r','implement','thread-1',{'a':'hash'},'model')
                self.assertFalse(supervisor.can_resume('r','implement','thread-1',{'a':'hash'},'model'))
                resumed.bind_session('thread-1');self.assertTrue(resumed.finish())
                self.assertTrue(supervisor.can_resume('r','implement','thread-1',{'a':'hash'},'model'))
            with patch('runner.local_supervisor.start_local_process',side_effect=RuntimeError('uncertain launch')):
                with self.assertRaises(RuntimeError): supervisor.start_execution('r','implement','thread-1',{'a':'hash'},'model')
            self.assertFalse(supervisor.can_resume('r','implement','thread-1',{'a':'hash'},'model'))
    def test_policy_must_be_verified(self,_sandbox,_scope):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('x');binary.chmod(0o700)
            supervisor=LocalSupervisor(root+'/db',str(binary),{'HOME':root},Backend(),lambda run:True,lambda run,role:root,lambda:False,{'mcp_servers':[],'plugins':[]})
            self.assertFalse(supervisor.preflight())
            with self.assertRaises(LocalSupervisorError): supervisor.start_execution('r','qa',None,{},'model')
    def test_failed_host_probe_blocks_execution(self,sandbox,_scope):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('x');binary.chmod(0o700)
            supervisor=LocalSupervisor(root+'/db',str(binary),{'HOME':root},Backend(),lambda run:True,lambda run,role:root,lambda:True,{'mcp_servers':[],'plugins':[]})
            sandbox.return_value=False
            self.assertFalse(supervisor.preflight())
            with self.assertRaises(LocalSupervisorError): supervisor.start_execution('r','qa',None,{},'model')
