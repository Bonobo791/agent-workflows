import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT=str(Path(__file__).parents[1]/'runner'/'local_command_child.py')
class LocalCommandChildTests(unittest.TestCase):
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
