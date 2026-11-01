import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runner.local_preflight import sandbox_preflight, scope_preflight

class SandboxPreflightTests(unittest.TestCase):
    def test_success_requires_all_probe_results(self):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            ok=subprocess.CompletedProcess([],0,stdout='private_denied=1\nworkspace_write=1\nnetwork_denied=1\n',stderr='')
            def probe(*args,**kwargs):
                (Path(kwargs['cwd'])/'probe-output').write_text('synthetic')
                return ok
            with patch('runner.local_preflight.subprocess.run',side_effect=probe) as run:
                self.assertTrue(sandbox_preflight(str(binary),{'HOME':root,'ACCESS_TOKEN':'synthetic-secret'}))
                self.assertNotIn('ACCESS_TOKEN',run.call_args.kwargs['env'])
            for output in ('private_denied=1\nworkspace_write=1\nnetwork_denied=0\n','private_denied=1\nworkspace_write=1\n'):
                with patch('runner.local_preflight.subprocess.run',return_value=subprocess.CompletedProcess([],0,stdout=output,stderr='')):
                    self.assertFalse(sandbox_preflight(str(binary),{'HOME':root}))
    def test_launch_failure_is_not_ready(self):
        with tempfile.TemporaryDirectory() as root:
            binary=Path(root)/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            with patch('runner.local_preflight.subprocess.run',side_effect=OSError('unavailable')):
                self.assertFalse(sandbox_preflight(str(binary),{'HOME':root}))
    def test_ambiguous_scope_launch_retains_state_without_unverified_stop(self):
        import io
        from unittest.mock import Mock
        from runner.local_preflight import scope_preflight
        created=[]
        real_temporary=tempfile.TemporaryDirectory
        def temporary(*args,**kwargs):
            item=real_temporary(*args,**kwargs); created.append(item); return item
        process=Mock(); process.stdout=io.StringIO('not-a-pid\n'); process.poll.return_value=None
        try:
            with patch('runner.local_preflight.tempfile.TemporaryDirectory',side_effect=temporary), \
                 patch('runner.local_preflight.subprocess.Popen',return_value=process), \
                 patch('select.select',return_value=([process.stdout],[],[])), \
                 patch('runner.local_scope.SystemdScopes.stop') as stop:
                self.assertFalse(scope_preflight({'HOME':'/tmp'}))
                self.assertEqual(len(created),1)
                self.assertTrue(Path(created[0].name,'state.db').exists())
                stop.assert_not_called()
        finally:
            for item in created: item.cleanup()

    def test_scope_launch_failure_is_not_ready(self):
        with patch('runner.local_preflight.subprocess.Popen',side_effect=OSError('unavailable')), patch('runner.local_scope.SystemdScopes.stop'):
            self.assertFalse(scope_preflight({'HOME':'/tmp'}))
