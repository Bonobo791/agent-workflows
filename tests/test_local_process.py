import tempfile
import unittest
import json
import os
from pathlib import Path
from unittest.mock import patch
from runner.local_process import LocalProcessError, start_local_process
from runner.local_scope import LocalScopes
from runner.local_launch import GuardedLaunch
from tests.test_local_scope import Backend

class FakeProcess:
    pid=123
    stdin=None
    stdout=None
    def poll(self): return None
    def wait(self,timeout): return 0

class LocalProcessTests(unittest.TestCase):
    def guarded_launch(self,backend,process,check=None):
        def launch(prefix,target,**kwargs):
            backend.unit=prefix[prefix.index('--unit')+1]
            backend.pid_in_cgroup=lambda pid,group:pid==123 and group=='/user.slice/'+backend.unit
            if check is not None:check(prefix+target,kwargs)
            ack_read,ack_write=os.pipe();go_read,go_write=os.pipe()
            self.last_go_read=go_read
            os.write(ack_write,json.dumps({'version':1,'nonce':'a'*64,'pid':123}).encode());os.close(ack_write)
            self.addCleanup(os.close,go_read)
            return GuardedLaunch(process,ack_read,go_write,'a'*64)
        return launch
    def test_scope_is_bound_and_stopped_before_success(self):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            work=Path(root)/'work';work.mkdir(mode=0o700)
            backend=Backend(); process=FakeProcess()
            def check(args,kwargs):
                self.assertEqual(kwargs['cwd'],str(work))
                self.assertNotIn('ACCESS_TOKEN',kwargs['env'])
            with patch('runner.local_process.start_guarded',side_effect=self.guarded_launch(backend,process,check)):
                handle=start_local_process(root+'/state.db','run',str(work),str(binary),{'HOME':root,'ACCESS_TOKEN':'secret'},backend,lambda:True,{'mcp_servers':[],'plugins':[]})
            self.assertEqual(os.read(self.last_go_read,1),b'G')
            self.assertTrue(Path(handle.output_dir).is_dir()); self.assertFalse(Path(handle.output_dir).is_relative_to(work))
            self.assertTrue(handle.assert_active());self.assertTrue(handle.finish())
            scopes=LocalScopes(root+'/state.db',backend)
            try:self.assertEqual(scopes.state('run',handle.unit),'terminated')
            finally:scopes.close()
    def test_untrusted_workspace_or_fence_prevents_launch(self):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            backend=Backend()
            with patch('runner.local_process.subprocess.Popen') as launch:
                with self.assertRaises(LocalProcessError): start_local_process(root+'/state.db','run',root,str(binary),{'HOME':root},backend,lambda:False,{'mcp_servers':[],'plugins':[]})
                launch.assert_not_called()
    def test_binding_failure_closes_authorization_pipe(self):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            work=Path(root)/'work';work.mkdir(mode=0o700)
            backend=Backend();process=FakeProcess();launches=[]
            start=self.guarded_launch(backend,process)
            def launch(*args,**kwargs):
                result=start(*args,**kwargs);launches.append(result);return result
            with patch('runner.local_process.start_guarded',side_effect=launch), patch.object(LocalScopes,'bind_confirmed',side_effect=RuntimeError('audit unavailable')):
                with self.assertRaises(LocalProcessError):
                    start_local_process(root+'/state.db','run',str(work),str(binary),{'HOME':root},backend,lambda:True,{'mcp_servers':[],'plugins':[]})
            self.assertEqual(os.read(self.last_go_read,1),b'')
    def test_failed_binding_reaps_real_guard_process(self):
        from runner.local_launch import start_guarded
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            work=Path(root)/'work';work.mkdir(mode=0o700);launched=[]
            def start(prefix,target,**kwargs):
                result=start_guarded([],target,**kwargs);launched.append(result);return result
            with patch('runner.local_process.start_guarded',side_effect=start),patch.object(LocalScopes,'bind_confirmed',side_effect=RuntimeError('audit unavailable')):
                with self.assertRaises(LocalProcessError):
                    start_local_process(root+'/state.db','run',str(work),str(binary),{'HOME':root},Backend(),lambda:True,{'mcp_servers':[],'plugins':[]})
            self.assertIsNotNone(launched[0].process.returncode)
            with self.assertRaises(ChildProcessError):os.waitpid(launched[0].process.pid,os.WNOHANG)
    def test_git_write_permission_is_explicit_and_scoped_to_checkout(self):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            work=Path(root)/'work';work.mkdir(mode=0o700);gitdir=work/'.git';gitdir.mkdir(mode=0o700)
            backend=Backend();process=FakeProcess()
            def check(args,kwargs):
                self.assertTrue(any('"'+str(gitdir)+'" = "write"' in arg for arg in args))
            with patch('runner.local_process.start_guarded',side_effect=self.guarded_launch(backend,process,check)):
                handle=start_local_process(root+'/state.db','run',str(work),str(binary),{'HOME':root},backend,lambda:True,{'mcp_servers':[],'plugins':[]},allow_git=True)
            self.assertEqual(handle.git_dir,str(gitdir));self.assertTrue(handle.finish())
