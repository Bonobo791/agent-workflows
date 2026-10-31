import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runner.local_process import LocalProcessError, start_local_process
from runner.local_scope import LocalScopes
from tests.test_local_scope import Backend

class FakeProcess:
    stdin=None
    stdout=None
    def poll(self): return None
    def wait(self,timeout): return 0

class LocalProcessTests(unittest.TestCase):
    def test_scope_is_bound_and_stopped_before_success(self):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            work=Path(root)/'work';work.mkdir(mode=0o700)
            backend=Backend(); process=FakeProcess()
            def launch(args,**kwargs):
                backend.unit=args[args.index('--unit')+1]
                self.assertEqual(kwargs['cwd'],str(work))
                self.assertNotIn('ACCESS_TOKEN',kwargs['env'])
                return process
            with patch('runner.local_process.subprocess.Popen',side_effect=launch):
                handle=start_local_process(root+'/state.db','run',str(work),str(binary),{'HOME':root,'ACCESS_TOKEN':'secret'},backend,lambda:True,{'mcp_servers':[],'plugins':[]})
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
    def test_git_write_permission_is_explicit_and_scoped_to_checkout(self):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            work=Path(root)/'work';work.mkdir(mode=0o700);gitdir=work/'.git';gitdir.mkdir(mode=0o700)
            backend=Backend();process=FakeProcess()
            def launch(args,**kwargs):
                backend.unit=args[args.index('--unit')+1]
                self.assertTrue(any('"'+str(gitdir)+'" = "write"' in arg for arg in args))
                return process
            with patch('runner.local_process.subprocess.Popen',side_effect=launch):
                handle=start_local_process(root+'/state.db','run',str(work),str(binary),{'HOME':root},backend,lambda:True,{'mcp_servers':[],'plugins':[]},allow_git=True)
            self.assertEqual(handle.git_dir,str(gitdir));self.assertTrue(handle.finish())
