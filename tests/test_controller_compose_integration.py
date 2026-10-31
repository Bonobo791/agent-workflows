"""Opt-in rootless Podman Compose startup with a private bind-mounted secret."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid


@unittest.skipUnless(os.environ.get('CONTROLLER_TEST_IMAGE') and os.environ.get('CONTROLLER_TEST_DOCKER_HOST'), 'controller image and Podman Compose socket not configured')
class ControllerComposeTests(unittest.TestCase):
    def test_private_secret_and_state_survive_stop_and_start(self):
        root=Path(__file__).resolve().parents[1]
        project='workflow-check-'+uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory(prefix='workflow-compose-') as directory:
            secret=Path(directory)/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            image_file=Path(directory)/'image.yaml'
            image_file.write_text(json.dumps({'services':{'workflow':{'image':os.environ['CONTROLLER_TEST_IMAGE']}}}))
            env={**os.environ,'CONTROL_SECRET_PATH':str(secret),'DOCKER_HOST':os.environ['CONTROLLER_TEST_DOCKER_HOST']}
            command=['docker-compose','-p',project,'-f',str(root/'compose.yaml'),'-f',str(root/'compose.podman.yaml'),'-f',str(image_file)]
            def compose(*args):
                return subprocess.run(command+list(args),env=env,capture_output=True,text=True,check=True,timeout=30)
            def request(container,action):
                code="import json,socket; s=socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect('/data/control.sock'); s.sendall(json.dumps({'secret':'x'*40,'request':"+repr(action)+"}).encode()+b'\\n'); print(s.makefile('rb').readline().decode())"
                result=subprocess.run(['podman','exec',container,'python','-c',code],capture_output=True,text=True,check=True,timeout=5)
                return json.loads(result.stdout)
            def wait_ready():
                container=compose('ps','-q','workflow').stdout.strip()
                self.assertTrue(container)
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    try:
                        if request(container,{'action':'metrics'})['ok']: return container
                    except (subprocess.SubprocessError,ValueError,KeyError): time.sleep(.2)
                logs=subprocess.run(['podman','logs',container],capture_output=True,text=True,timeout=5)
                self.fail('controller socket unavailable: '+logs.stdout+logs.stderr)
            try:
                compose('up','-d','--no-build')
                container=wait_ready()
                self.assertTrue(request(container,{'action':'create','run':'r','workflow':'marketing','task':'t'})['ok'])
                compose('stop','workflow')
                compose('start','workflow')
                container=wait_ready()
                self.assertTrue(request(container,{'action':'status','run':'r','stage':'intake'})['ok'])
            finally:
                subprocess.run(command+['down','--volumes','--remove-orphans'],env=env,capture_output=True,timeout=30)
