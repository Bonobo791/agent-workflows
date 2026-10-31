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
    def test_scope_launch_failure_is_not_ready(self):
        with patch('runner.local_preflight.subprocess.Popen',side_effect=OSError('unavailable')), patch('runner.local_scope.SystemdScopes.stop'):
            self.assertFalse(scope_preflight({'HOME':'/tmp'}))
