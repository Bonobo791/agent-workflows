"""Exercise the shipped worker process and authenticated Unix socket locally."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

class WorkerProcessTests(unittest.TestCase):
    def test_real_worker_socket_authentication_and_control(self):
        with tempfile.TemporaryDirectory() as root:
            secret=Path(root)/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            env=os.environ.copy(); env.update(CONTROL_SECRET_FILE=str(secret),WORKFLOW_DATA_DIR=root,WORKFLOW_EXECUTION_MODE='disabled')
            process=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                path=str(Path(root)/'control.sock'); deadline=time.monotonic()+10
                while not Path(path).exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                self.assertTrue(Path(path).is_socket(), 'worker socket was not created')
                def request(token,body):
                    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                        client.settimeout(3); client.connect(path)
                        client.sendall(json.dumps({'secret':token,'request':body}).encode()+b'\n')
                        with client.makefile('rb') as stream: return json.loads(stream.readline())
                self.assertFalse(request('wrong',{'action':'create','run':'r','workflow':'marketing','task':'t'})['ok'])
                self.assertFalse(request('x'*40,{'action':'start_configured_auto'})['ok'])
                self.assertTrue(request('x'*40,{'action':'create','run':'r','workflow':'marketing','task':'t'})['ok'])
                self.assertTrue(request('x'*40,{'action':'status','run':'r','stage':'intake'})['ok'])
            finally:
                process.terminate()
                try: process.communicate(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.communicate(timeout=5)
            self.assertEqual(process.returncode,0)
