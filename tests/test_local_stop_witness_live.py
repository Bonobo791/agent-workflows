"""Opt-in process-crash test for the production stop-witness executable."""
import json
import os
from pathlib import Path
import select
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from runner.local_scope import LocalScopes,SystemdScopes
from tests.test_stop_witness_live import require_pidfd,finish_pidfd


@unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
class LocalStopWitnessLiveTests(unittest.TestCase):
    def test_owner_recovers_normal_stop_after_worker_crash(self):
        self._exercise_owner_recovery(False)

    def test_killed_witness_leaves_interrupted_stop_fenced(self):
        if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):self.skipTest('pidfd required')
        self._exercise_owner_recovery(True)

    def _exercise_owner_recovery(self,kill_witness):
        require_pidfd(self)
        from runner.control import ControlError,ControlPlane
        from runner.manual_lifecycle import ManualLifecycle
        from runner.service import Service
        from types import SimpleNamespace
        target='''import signal,sys,time
from pathlib import Path
def stop(*_):
 Path(sys.argv[1]).touch()
 while not Path(sys.argv[2]).exists():time.sleep(.02)
 sys.exit(0)
signal.signal(signal.SIGTERM,stop);Path(sys.argv[3]).touch()
while True:time.sleep(.05)
'''
        caller='''import os,sys
from runner.local_scope import LocalScopes,SystemdScopes
scopes=LocalScopes(sys.argv[1],SystemdScopes(os.environ));scopes.stop('run',sys.argv[2])
'''
        root=Path(tempfile.mkdtemp(prefix='workflow-stop-worker-'));backend=SystemdScopes(os.environ)
        service=Service(root);lifecycle=ManualLifecycle(str(root/'state.db'),'target','inc');service.lifecycle=lifecycle
        service.create('run','marketing','task');service.enqueue('run');lifecycle.claim()
        service.model_supervisor=SimpleNamespace(backend=backend);control=ControlPlane(service,'secret')
        scopes=LocalScopes(str(root/'state.db'),backend);unit=scopes.reserve('run')
        ready,received,release=(root/name for name in ('ready','received','release'))
        launcher=None;worker=None;group=None;safe=False;witness_fd=None;client_fd=None
        try:
            launcher=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--',sys.executable,'-c',target,str(received),str(release),str(ready)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+10
            while not ready.exists() and launcher.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(ready.exists());scopes.bind('run',unit);group=backend.inspect(unit)['cgroup']
            worker=subprocess.Popen([sys.executable,'-c',caller,str(root/'state.db'),unit],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+10
            while not received.exists() and worker.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(received.exists())
            if kill_witness:
                witness_ids=(Path('/proc')/str(worker.pid)/'task'/str(worker.pid)/'children').read_text().split()
                self.assertEqual(len(witness_ids),1);witness_pid=int(witness_ids[0])
                self.assertIn(b'local_stop_witness.py',(Path('/proc')/str(witness_pid)/'cmdline').read_bytes())
                client_ids=(Path('/proc')/str(witness_pid)/'task'/str(witness_pid)/'children').read_text().split()
                self.assertEqual(len(client_ids),1);client_pid=int(client_ids[0])
                self.assertIn(b'systemctl',(Path('/proc')/str(client_pid)/'cmdline').read_bytes())
                witness_fd=os.pidfd_open(witness_pid);client_fd=os.pidfd_open(client_pid)
                signal.pidfd_send_signal(witness_fd,signal.SIGKILL)
                self.assertTrue(select.select([witness_fd],[],[],5)[0]);self.assertFalse(select.select([client_fd],[],[],0)[0])
            if worker.poll() is None:worker.kill()
            worker.wait(timeout=5)
            self.assertEqual(scopes.state('run',unit),'stopping');service.request_lifecycle_stop('run')
            request={'action':'reconcile_local_scope','run':'run','unit':unit}
            with self.assertRaises(ControlError):control.handle('secret',request)
            release.touch();deadline=time.monotonic()+10
            if kill_witness:
                self.assertTrue(select.select([client_fd],[],[],10)[0])
                self.assertEqual(scopes.db.execute('SELECT status FROM local_scope_stop_operations WHERE unit=?',(unit,)).fetchone(),('active',))
                with self.assertRaises(ControlError):control.handle('secret',request)
                self.assertEqual(scopes.state('run',unit),'stopping');self.assertTrue(backend.empty_cgroup(group));safe=True
                return
            while time.monotonic()<deadline:
                state=scopes.db.execute('SELECT status FROM local_scope_stop_operations WHERE unit=?',(unit,)).fetchone()
                if state==('completed',):break
                time.sleep(.05)
            self.assertEqual(state,('completed',))
            self.assertEqual(control.handle('secret',request),{'unit':unit,'state':'terminated'})
            self.assertEqual(lifecycle.status('run')['status'],'pending');self.assertTrue(backend.empty_cgroup(group));safe=True
        finally:
            release.touch()
            if worker is not None and worker.poll() is None:worker.kill();worker.wait(timeout=5)
            if witness_fd is not None:finish_pidfd(witness_fd);os.close(witness_fd)
            if client_fd is not None:finish_pidfd(client_fd);os.close(client_fd)
            if launcher is not None:
                try:launcher.wait(timeout=5)
                except subprocess.TimeoutExpired:launcher.kill();launcher.wait(timeout=5)
            scopes.close();lifecycle.close();service.close()
            if safe:shutil.rmtree(root)

    def test_witness_records_stop_after_its_parent_is_killed(self):
        require_pidfd(self)
        target='''import signal,sys,time
from pathlib import Path
def stop(*_):
 Path(sys.argv[1]).touch()
 while not Path(sys.argv[2]).exists():time.sleep(.02)
 sys.exit(0)
signal.signal(signal.SIGTERM,stop);Path(sys.argv[3]).touch()
while True:time.sleep(.05)
'''
        caller='''import json,os,sys,time
from pathlib import Path
from runner.local_stop import start_stop_witness
request,marker=sys.argv[1:]
child=start_stop_witness(**json.loads(request),environment=os.environ)
Path(marker+'.tmp').write_text(str(child.pid));os.replace(marker+'.tmp',marker)
while True:time.sleep(.1)
'''
        root=Path(tempfile.mkdtemp(prefix='workflow-stop-receipt-'));backend=SystemdScopes(os.environ)
        scopes=LocalScopes(str(root/'state.db'),backend);unit=scopes.reserve('run')
        ready,received,release,started=(root/name for name in ('ready','received','release','started'))
        launcher=None;worker=None;witness_fd=None;group=None;safe=False;finished=False
        try:
            launcher=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--',sys.executable,'-c',target,str(received),str(release),str(ready)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+10
            while not ready.exists() and launcher.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(ready.exists());scopes.bind('run',unit)
            info=backend.inspect(unit);group=info['cgroup'];nonce=scopes.reserve_stop('run',unit)
            request=json.dumps({'path':str(root/'state.db'),'run':'run','unit':unit,'nonce':nonce})
            worker=subprocess.Popen([sys.executable,'-c',caller,request,str(started)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+10
            while not (started.exists() and received.exists()) and worker.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(started.exists() and received.exists());witness_fd=os.pidfd_open(int(started.read_text()))
            worker.kill();self.assertEqual(worker.wait(timeout=5),-9)
            self.assertEqual(scopes.db.execute('SELECT status FROM local_scope_stop_operations').fetchone(),('active',))
            self.assertFalse(select.select([witness_fd],[],[],0)[0]);release.touch()
            finished=bool(select.select([witness_fd],[],[],10)[0]);self.assertTrue(finished)
            self.assertEqual(scopes.db.execute('SELECT status FROM local_scope_stop_operations').fetchone(),('completed',))
            self.assertEqual(scopes.db.execute('SELECT status FROM local_scope_stop_audit ORDER BY id').fetchall(),[('pending',),('active',),('completed',)])
            self.assertEqual(backend.inspect(unit)['active'],'inactive');self.assertTrue(backend.empty_cgroup(group))
            from tests.test_local_launch import add_recovery_fence
            add_recovery_fence(scopes)
            self.assertTrue(scopes.reconcile_witnessed_stop('run',unit,('target','inc')))
            self.assertEqual(scopes.state('run',unit),'terminated');safe=True
        finally:
            release.touch()
            if worker is not None and worker.poll() is None:worker.kill();worker.wait(timeout=5)
            if witness_fd is not None:
                finished=finish_pidfd(witness_fd);os.close(witness_fd)
            if not safe and (witness_fd is None or finished):
                try:
                    observed=backend.inspect(unit);group=group or observed.get('cgroup')
                    if observed['active']=='active':backend.stop(unit)
                    observed=backend.inspect(unit);safe=bool(group and observed['active']=='inactive' and backend.empty_cgroup(group))
                except Exception:pass
            if launcher is not None:
                try:launcher.wait(timeout=5)
                except subprocess.TimeoutExpired:launcher.kill();launcher.wait(timeout=5)
            scopes.close()
            if safe:shutil.rmtree(root)
