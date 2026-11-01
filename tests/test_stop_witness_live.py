"""Explore evidence for a stop client that outlives its caller."""
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

from runner.local_scope import LocalScopes, SystemdScopes


@unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
class StopWitnessLiveTests(unittest.TestCase):
    def test_stop_witness_outlives_crashed_caller_and_records_only_after_stop(self):
        self._exercise_witness(False)

    def test_killed_witness_does_not_claim_an_inflight_stop_finished(self):
        self._exercise_witness(True)

    def _exercise_witness(self,kill_witness):
        if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
            self.skipTest('pidfd required for safe cleanup')
        target='''import signal,sys,time
from pathlib import Path
def stop(*_):
 Path(sys.argv[1]).touch()
 while not Path(sys.argv[2]).exists():time.sleep(.02)
 sys.exit(0)
signal.signal(signal.SIGTERM,stop);Path(sys.argv[3]).touch()
while True:time.sleep(.05)
'''
        witness='''import os,subprocess,sys
from pathlib import Path
unit,started,done=sys.argv[1:]
process=subprocess.Popen(['/usr/bin/systemctl','--user','stop',unit],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
Path(started+'.tmp').write_text(str(os.getpid())+' '+str(process.pid));os.replace(started+'.tmp',started)
if process.wait()==0:
 fd=os.open(done,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:os.write(fd,b'completed');os.fsync(fd)
 finally:os.close(fd)
'''
        caller='''import subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[2:]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
while True:time.sleep(.1)
'''
        root=Path(tempfile.mkdtemp(prefix='workflow-stop-witness-'))
        backend=SystemdScopes(os.environ);scopes=LocalScopes(str(root/'state.db'),backend)
        unit=scopes.reserve('run');ready,received,release,started,done=(root/name for name in ('ready','received','release','started','done'))
        launcher=None;worker=None;witness_fd=None;client_fd=None;group=None;safe=False;witness_finished=False;client_finished=False
        try:
            launcher=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--',sys.executable,'-c',target,str(received),str(release),str(ready)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+10
            while not ready.exists() and launcher.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(ready.exists());scopes.bind('run',unit);group=backend.inspect(unit)['cgroup']
            worker=subprocess.Popen([sys.executable,'-c',caller,witness,unit,str(started),str(done)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+10
            while not (started.exists() and received.exists()) and worker.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(started.exists() and received.exists())
            witness_pid,client_pid=map(int,started.read_text().split())
            witness_fd=os.pidfd_open(witness_pid);client_fd=os.pidfd_open(client_pid)
            worker.kill();self.assertEqual(worker.wait(timeout=5),-9)
            self.assertFalse(done.exists());self.assertFalse(select.select([witness_fd],[],[],0)[0])
            if kill_witness:
                signal.pidfd_send_signal(witness_fd,signal.SIGKILL)
                self.assertTrue(select.select([witness_fd],[],[],5)[0])
                self.assertFalse(done.exists());self.assertFalse(select.select([client_fd],[],[],0)[0])
            release.touch()
            client_finished=bool(select.select([client_fd],[],[],10)[0]);self.assertTrue(client_finished)
            witness_finished=bool(select.select([witness_fd],[],[],10)[0])
            self.assertTrue(witness_finished)
            if kill_witness:self.assertFalse(done.exists())
            else:self.assertEqual(done.read_bytes(),b'completed')
            self.assertEqual(backend.inspect(unit)['active'],'inactive');self.assertTrue(backend.empty_cgroup(group));safe=True
        finally:
            release.touch()
            if worker is not None and worker.poll() is None:worker.kill();worker.wait(timeout=5)
            if witness_fd is not None:
                witness_finished=bool(select.select([witness_fd],[],[],5)[0])
                os.close(witness_fd)
            if client_fd is not None:
                client_finished=bool(select.select([client_fd],[],[],5)[0])
                os.close(client_fd)
            if not safe and (witness_fd is None or witness_finished) and (client_fd is None or client_finished):
                try:
                    observed=backend.inspect(unit);group=group or observed.get('cgroup')
                    if observed['active']=='active':backend.stop(unit)
                    observed=backend.inspect(unit)
                    safe=bool(group and observed['active']=='inactive' and backend.empty_cgroup(group))
                except Exception:pass
            if launcher is not None:
                try:launcher.wait(timeout=5)
                except subprocess.TimeoutExpired:launcher.kill();launcher.wait(timeout=5)
            scopes.close()
            if safe:shutil.rmtree(root)
