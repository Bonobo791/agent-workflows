import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT=str(Path(__file__).parents[1]/'runner'/'local_command_child.py')
class LocalCommandChildTests(unittest.TestCase):
    def test_relay_and_transport_use_the_same_frame_limit(self):
        from runner.local_command_child import MAX_LINE as relay_limit
        from runner.stdio import MAX_LINE as transport_limit
        self.assertEqual(relay_limit,transport_limit)
    def run_child(self,command,limit=1024,timeout=3,input_data=None):
        request={'argv':command,'cwd':'/tmp','environment':{'PATH':'/usr/bin:/bin'},'timeout':timeout,'output_limit':limit}
        if input_data is not None:request['stdin_b64']=base64.b64encode(input_data).decode()
        process=subprocess.run([sys.executable,'-I',SCRIPT],input=json.dumps(request)+'\n',capture_output=True,text=True,timeout=8)
        return process.returncode,[json.loads(line) for line in process.stdout.splitlines()]
    def test_captures_real_stdout_stderr_and_exit(self):
        code='import sys; print("hello"); print("bad",file=sys.stderr); sys.exit(7)'
        status,events=self.run_child([sys.executable,'-c',code])
        self.assertEqual(status,0);self.assertEqual(events[0],{'ready':True})
        self.assertEqual(base64.b64decode(events[1]['stdout']),b'hello\n');self.assertEqual(base64.b64decode(events[2]['stderr']),b'bad\n');self.assertEqual(events[3],{'exit_code':7})
    def test_output_overflow_is_not_a_completed_result(self):
        _,events=self.run_child([sys.executable,'-c','print("x"*2000)'],limit=100)
        self.assertEqual(events[-1],{'error':'command unavailable or limit exceeded'})
        self.assertFalse(any('exit_code' in event for event in events))
    def test_timeout_is_not_a_completed_result(self):
        _,events=self.run_child([sys.executable,'-c','import time; time.sleep(5)'],timeout=1)
        self.assertEqual(events[-1],{'error':'command unavailable or limit exceeded'})
        self.assertFalse(any('exit_code' in event for event in events))
    def test_private_input_is_delivered_through_stdin(self):
        status,events=self.run_child([sys.executable,'-c','import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'],input_data=b'synthetic body')
        self.assertEqual(status,0);self.assertEqual(base64.b64decode(events[1]['stdout']),b'synthetic body')
    def test_valid_large_escaped_command_is_not_truncated(self):
        argv=['/bin/true']+['é'*4096]*20
        self.assertGreater(len(json.dumps({'argv':argv})),65537)
        status,events=self.run_child(argv)
        self.assertEqual(status,0)
        self.assertEqual(events[-1],{'exit_code':0})
    def test_oversized_frame_does_not_start_command(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'executed'
            request={'argv':[sys.executable,'-c',f'open({str(marker)!r},"w").write("bad")'],'cwd':root,'environment':{'padding':'x'*(2*1024*1024)},'timeout':3,'output_limit':1024}
            result=subprocess.run([sys.executable,'-I',SCRIPT],input=json.dumps(request)+'\n',capture_output=True,text=True,timeout=8)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]),{'error':'command unavailable or limit exceeded'})
            self.assertFalse(marker.exists())
    def test_failed_relay_waits_for_controller_before_exit(self):
        process=subprocess.Popen([sys.executable,'-I',SCRIPT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(json.loads(process.stdout.readline()),{'ready':True})
            process.stdin.write('{}\n'); process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline()),{'error':'command unavailable or limit exceeded'})
            self.assertIsNone(process.poll())
            process.stdin.write('\n'); process.stdin.flush()
            self.assertEqual(process.wait(timeout=3),0)
        finally:
            if process.poll() is None: process.kill(); process.wait()
            process.stdin.close(); process.stdout.close(); process.stderr.close()
