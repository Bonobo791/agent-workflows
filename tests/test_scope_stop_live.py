"""Opt-in recovery check against a disposable user-systemd scope."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from runner.local_scope import LocalScopes, ScopeError, SystemdScopes


@unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD') == '1', 'real user systemd scope not configured')
class InterruptedStopTests(unittest.TestCase):
    def test_killed_before_stop_request(self):
        self._exercise_stop_crash('before')

    def test_killed_after_stop_completed(self):
        self._exercise_stop_crash('after')

    def _exercise_stop_crash(self, phase):
        code = '''
import sys,time
from pathlib import Path
from runner.local_scope import LocalScopes,SystemdScopes
class DelayedStop(SystemdScopes):
    def stop(self,unit):
        if sys.argv[4]=='after': super().stop(unit)
        Path(sys.argv[3]).write_text(sys.argv[4])
        time.sleep(60)
        return True
scopes=LocalScopes(sys.argv[1],DelayedStop(__import__('os').environ))
scopes.stop('run',sys.argv[2])
'''
        with tempfile.TemporaryDirectory() as root:
            state = str(Path(root) / 'state.db')
            marker = Path(root) / 'before-stop'
            backend = SystemdScopes(os.environ)
            scopes = LocalScopes(state, backend)
            unit = scopes.reserve('run')
            launcher = subprocess.Popen(['/usr/bin/systemd-run', '--user', '--scope', '--quiet', '--unit', unit, '--', '/usr/bin/sleep', '120'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            worker = None
            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        scopes.bind('run', unit)
                        break
                    except ScopeError:
                        if launcher.poll() is not None or time.monotonic() >= deadline:
                            raise
                        time.sleep(.05)
                group = backend.inspect(unit)['cgroup']
                worker = subprocess.Popen([sys.executable, '-c', code, state, unit, str(marker), phase], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 10
                while not marker.exists() and worker.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.exists(), 'stop caller did not reach the request boundary')
                worker.kill()
                worker.wait(timeout=5)
                self.assertEqual(scopes.state('run', unit), 'stopping')
                self.assertEqual(backend.inspect(unit)['active'], 'active' if phase == 'before' else 'inactive')
                with self.assertRaises(ScopeError):
                    scopes.reconcile_stop('run', unit, lambda *_: False)
                self.assertEqual(scopes.state('run', unit), 'stopping')
                if phase == 'before':
                    self.assertTrue(backend.stop(unit))
                self.assertEqual(backend.inspect(unit)['active'], 'inactive')
                self.assertTrue(backend.empty_cgroup(group))
                self.assertTrue(scopes.reconcile_stop('run', unit, lambda *_: worker.poll() is not None))
                self.assertEqual(scopes.state('run', unit), 'terminated')
            finally:
                if worker is not None and worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=5)
                subprocess.run(['/usr/bin/systemctl', '--user', 'stop', unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
                try:
                    launcher.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    launcher.kill()
                    launcher.wait(timeout=5)
                scopes.close()
