"""Tests for a private, fail-closed launch handshake; no recovery is authorized here."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

GUARD=Path(__file__).resolve().parents[1]/'runner'/'local_launch_guard.py'


class LocalLaunchGuardTests(unittest.TestCase):
    def test_parent_can_authorize_after_slow_scope_binding(self):
        ack_read,ack_write=os.pipe();go_read,go_write=os.pipe();process=None
        try:
            process=subprocess.Popen([sys.executable,'-I',str(GUARD),str(ack_write),str(go_read),'a'*64,
                sys.executable,'-c','pass'],pass_fds=(ack_write,go_read),stderr=subprocess.DEVNULL)
            os.close(ack_write);ack_write=None;os.close(go_read);go_read=None
            self.assertTrue(select.select([ack_read],[],[],5)[0]);os.read(ack_read,1024)
            time.sleep(10.2)
            self.assertIsNone(process.poll(),'guard expired before the parent completed binding')
            os.write(go_write,b'G');os.close(go_write);go_write=None
            self.assertEqual(process.wait(timeout=5),0)
        finally:
            for fd in (ack_read,ack_write,go_read,go_write):
                if fd is not None:os.close(fd)
            if process is not None and process.poll() is None:process.kill();process.wait(timeout=5)

    def test_target_waits_for_go_and_does_not_inherit_receipt_pipe(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'executed';ack_read,ack_write=os.pipe();go_read,go_write=os.pipe();nonce='a'*64
            process=None
            try:
                target='from pathlib import Path; import time; Path('+repr(str(marker))+').touch(); time.sleep(1)'
                process=subprocess.Popen([sys.executable,'-I',str(GUARD),str(ack_write),str(go_read),nonce,
                    sys.executable,'-c',target],pass_fds=(ack_write,go_read),
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                os.close(ack_write);ack_write=None;os.close(go_read);go_read=None
                ready,_,_=select.select([ack_read],[],[],5)
                self.assertTrue(ready,'launch receipt unavailable')
                self.assertEqual(json.loads(os.read(ack_read,1024)),{'version':1,'nonce':nonce,'pid':process.pid})
                self.assertIsNone(process.poll());self.assertFalse(marker.exists())
                os.write(go_write,b'G');os.close(go_write);go_write=None
                deadline=time.monotonic()+5
                while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(marker.exists())
                ready,_,_=select.select([ack_read],[],[],.5)
                self.assertTrue(ready,'target inherited receipt pipe')
                self.assertEqual(os.read(ack_read,1),b'')
                self.assertEqual(process.wait(timeout=5),0)
            finally:
                for fd in (ack_read,ack_write,go_read,go_write):
                    if fd is not None:os.close(fd)
                if process is not None:
                    if process.poll() is None:process.kill();process.wait(timeout=5)
                    process.stdout.close();process.stderr.close()

    def test_closed_go_pipe_prevents_target_execution(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'executed';ack_read,ack_write=os.pipe();go_read,go_write=os.pipe()
            process=None
            try:
                target='from pathlib import Path; Path('+repr(str(marker))+').touch()'
                process=subprocess.Popen([sys.executable,'-I',str(GUARD),str(ack_write),str(go_read),'a'*64,
                    sys.executable,'-c',target],pass_fds=(ack_write,go_read),
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                os.close(ack_write);ack_write=None;os.close(go_read);go_read=None
                self.assertTrue(select.select([ack_read],[],[],5)[0]);self.assertTrue(os.read(ack_read,1024))
                os.close(go_write);go_write=None
                process.communicate(timeout=5)
                self.assertNotEqual(process.returncode,0);self.assertFalse(marker.exists())
            finally:
                for fd in (ack_read,ack_write,go_read,go_write):
                    if fd is not None:os.close(fd)
                if process is not None:
                    if process.poll() is None:process.kill();process.wait(timeout=5)
                    process.stdout.close();process.stderr.close()

    def test_invalid_receipt_inputs_do_not_execute_target(self):
        self.assertTrue(GUARD.is_file())
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'executed';ack_read,ack_write=os.pipe();go_read,go_write=os.pipe()
            try:
                target='from pathlib import Path; Path('+repr(str(marker))+').touch()'
                result=subprocess.run([sys.executable,'-I',str(GUARD),str(ack_write),str(go_read),'bad-nonce',
                    sys.executable,'-c',target],pass_fds=(ack_write,go_read),
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
                self.assertNotEqual(result.returncode,0);self.assertFalse(marker.exists())
            finally:
                for fd in (ack_read,ack_write,go_read,go_write):os.close(fd)

    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_handshake_pipe_survives_real_scope_launch(self):
        from runner.local_scope import SystemdScopes
        unit='agent-workflows-receipt-'+uuid.uuid4().hex+'.scope'
        backend=SystemdScopes(os.environ);ack_read,ack_write=os.pipe();go_read,go_write=os.pipe();nonce='b'*64;process=None
        try:
            process=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,
                '--',sys.executable,'-I',str(GUARD),str(ack_write),str(go_read),nonce,
                sys.executable,'-c','import time; time.sleep(2)'],
                pass_fds=(ack_write,go_read),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            os.close(ack_write);ack_write=None;os.close(go_read);go_read=None
            self.assertTrue(select.select([ack_read],[],[],10)[0],'scope launch receipt unavailable')
            self.assertEqual(json.loads(os.read(ack_read,1024)),{'version':1,'nonce':nonce,'pid':process.pid})
            observed=backend.inspect(unit)
            self.assertEqual((observed['id'],observed['load'],observed['active']),(unit,'loaded','active'))
            membership=(Path('/proc')/str(process.pid)/'cgroup').read_text().splitlines()
            self.assertIn('0::'+observed['cgroup'],membership)
            os.write(go_write,b'G');os.close(go_write);go_write=None
        finally:
            for fd in (ack_read,ack_write,go_read,go_write):
                if fd is not None:os.close(fd)
            if process is not None and process.poll() is None:
                subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_caller_crash_before_go_does_not_start_target(self):
        from runner.local_scope import SystemdScopes
        code='''import json,os,select,subprocess,sys,time
guard,unit,ready,marker=sys.argv[1:]
ack_read,ack_write=os.pipe();go_read,go_write=os.pipe();nonce='c'*64
target='from pathlib import Path; Path('+repr(marker)+').touch()'
process=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,
 '--',sys.executable,'-I',guard,str(ack_write),str(go_read),nonce,sys.executable,'-c',target],
 pass_fds=(ack_write,go_read),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
os.close(ack_write);os.close(go_read)
if not select.select([ack_read],[],[],10)[0]:sys.exit(2)
receipt=json.loads(os.read(ack_read,1024))
if receipt!={'version':1,'nonce':nonce,'pid':process.pid}:sys.exit(3)
open(ready,'w').write('ready')
time.sleep(60)
'''
        unit='agent-workflows-receipt-'+uuid.uuid4().hex+'.scope';backend=SystemdScopes(os.environ)
        with tempfile.TemporaryDirectory(prefix='workflow-launch-guard-') as directory:
            root=Path(directory);ready=root/'ready';marker=root/'executed';caller=None
            try:
                caller=subprocess.Popen([sys.executable,'-c',code,str(GUARD),unit,str(ready),str(marker)],
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                deadline=time.monotonic()+15
                while not ready.exists() and caller.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(ready.exists(),'caller did not receive the launch receipt')
                observed=backend.inspect(unit)
                self.assertEqual((observed['id'],observed['load'],observed['active']),(unit,'loaded','active'))
                cgroup=observed['cgroup'];self.assertTrue(cgroup)
                caller.kill();self.assertEqual(caller.wait(timeout=5),-9)
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    observed=backend.inspect(unit)
                    if observed['active']=='inactive' and backend.empty_cgroup(cgroup):break
                    time.sleep(.05)
                self.assertEqual(observed['active'],'inactive')
                self.assertTrue(backend.empty_cgroup(cgroup))
                self.assertFalse(marker.exists(),'target ran without a go signal')
            finally:
                if caller is not None and caller.poll() is None:caller.kill();caller.wait(timeout=5)
                subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
