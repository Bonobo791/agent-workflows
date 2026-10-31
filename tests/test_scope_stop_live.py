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

    def test_killed_with_stop_request_in_flight(self):
        import select
        import signal
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            self.skipTest('pidfd required for safe fixture cleanup')
        try:
            probe = os.pidfd_open(os.getpid())
        except OSError:
            self.skipTest('pidfd unavailable')
        os.close(probe)
        target_code = '''
import signal,sys,time
from pathlib import Path
def stop(*_):
    Path(sys.argv[1]).write_text('received')
    while not Path(sys.argv[2]).exists(): time.sleep(.02)
    sys.exit(0)
signal.signal(signal.SIGTERM,stop)
Path(sys.argv[3]).write_text('ready')
while True: time.sleep(.1)
'''
        caller_code = '''
import os,subprocess,sys
from pathlib import Path
from runner.local_scope import LocalScopes,SystemdScopes
class HeldStop(SystemdScopes):
    def stop(self,unit):
        child=subprocess.Popen(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        Path(sys.argv[3]).write_text(str(child.pid))
        return child.wait()==0
scopes=LocalScopes(sys.argv[1],HeldStop(os.environ))
scopes.stop('run',sys.argv[2])
'''
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            state = str(root / 'state.db')
            ready, received, release, stop_pid = (root / name for name in ('ready', 'received', 'release', 'stop-pid'))
            backend = SystemdScopes(os.environ)
            scopes = LocalScopes(state, backend)
            unit = scopes.reserve('run')
            launcher = subprocess.Popen(['/usr/bin/systemd-run', '--user', '--scope', '--quiet', '--unit', unit, '--', sys.executable, '-c', target_code, str(received), str(release), str(ready)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            worker = None
            fd = None
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and launcher.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists(), 'scope did not start')
                scopes.bind('run', unit)
                group = backend.inspect(unit)['cgroup']
                worker = subprocess.Popen([sys.executable, '-c', caller_code, state, unit, str(stop_pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 10
                while not (stop_pid.exists() and received.exists()) and worker.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(stop_pid.exists() and received.exists(), 'stop request did not reach the scope')
                fd = os.pidfd_open(int(stop_pid.read_text()))
                self.assertFalse(select.select([fd], [], [], 0)[0], 'stop client exited before interruption')
                worker.kill()
                worker.wait(timeout=5)
                self.assertEqual(scopes.state('run', unit), 'stopping')
                with self.assertRaises(ScopeError):
                    scopes.reconcile_stop('run', unit, lambda *_: bool(select.select([fd], [], [], 0)[0]))
                self.assertFalse(select.select([fd], [], [], 0)[0], 'stop request did not stay in flight')
                release.touch()
                self.assertTrue(select.select([fd], [], [], 10)[0], 'stop client did not exit')
                self.assertEqual(backend.inspect(unit)['active'], 'inactive')
                self.assertTrue(backend.empty_cgroup(group))
                self.assertTrue(scopes.reconcile_stop('run', unit, lambda *_: bool(select.select([fd], [], [], 0)[0])))
                self.assertEqual(scopes.state('run', unit), 'terminated')
            finally:
                release.touch()
                if worker is not None and worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=5)
                if fd is not None:
                    if not select.select([fd], [], [], 5)[0]:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                    os.close(fd)
                subprocess.run(['/usr/bin/systemctl', '--user', 'stop', unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
                try:
                    launcher.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    launcher.kill()
                    launcher.wait(timeout=5)
                scopes.close()
