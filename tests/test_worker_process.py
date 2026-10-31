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
    def test_local_mode_starts_only_with_private_pinned_configuration(self):
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            secret=Path(root)/'secret';secret.write_text('x'*40);secret.chmod(0o600)
            binary=Path(root)/'codex';binary.write_bytes(b'synthetic');binary.chmod(0o700)
            config=Path(root)/'local.json';config.write_text(json.dumps({'codex_executable':str(binary),'codex_sha256':hashlib.sha256(b'synthetic').hexdigest(),'disabled_integrations':{'mcp_servers':[],'plugins':[]}}));config.chmod(0o600)
            env={**os.environ,'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_DATA_DIR':root,'WORKFLOW_EXECUTION_MODE':'local','WORKFLOW_LIFECYCLE_MODE':'manual','MANUAL_WORKER_TARGET':'test','MANUAL_WORKER_INCARNATION':'one','LOCAL_RUNTIME_CONFIG_FILE':str(config)}
            process=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                path=Path(root)/'control.sock';deadline=time.monotonic()+10
                while not path.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(path.is_socket())
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                    client.settimeout(3);client.connect(str(path));client.sendall(json.dumps({'secret':'x'*40,'request':{'action':'create','run':'r','workflow':'marketing','task':'t'}}).encode()+b'\n')
                    with client.makefile('rb') as stream:self.assertTrue(json.loads(stream.readline())['ok'])
            finally:
                process.terminate()
                try: stdout,stderr=process.communicate(timeout=5)
                except subprocess.TimeoutExpired:process.kill();stdout,stderr=process.communicate(timeout=5)
            self.assertEqual(process.returncode,0,stderr.decode());self.assertIn(b'execution mode: local',stdout)
            data=json.loads(config.read_text());data['codex_sha256']='0'*64;config.write_text(json.dumps(data))
            failed=subprocess.run([sys.executable,'-m','runner.worker'],env=env,capture_output=True,timeout=10)
            self.assertNotEqual(failed.returncode,0);self.assertNotIn(b'x'*40,failed.stderr)
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
                self.assertIn('workflow_expired_leases 0',request('x'*40,{'action':'metrics'})['result']['metrics'])
                metrics=subprocess.run([sys.executable,'-m','runner.metrics_cli'],env=env,capture_output=True,text=True,timeout=5)
                self.assertEqual(metrics.returncode,0); self.assertIn('workflow_expired_leases 0',metrics.stdout)
                self.assertNotIn('x'*40,metrics.stdout+metrics.stderr)
                wrong=Path(root)/'wrong'; wrong.write_text('y'*40); wrong.chmod(0o600)
                failed=subprocess.run([sys.executable,'-m','runner.metrics_cli'],env={**env,'CONTROL_SECRET_FILE':str(wrong)},capture_output=True,text=True,timeout=5)
                self.assertNotEqual(failed.returncode,0); self.assertEqual(failed.stdout,''); self.assertNotIn('y'*40,failed.stderr)
            finally:
                process.terminate()
                try: process.communicate(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.communicate(timeout=5)
            self.assertEqual(process.returncode,0)
