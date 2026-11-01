"""Opt-in rootless Podman Compose startup with a private bind-mounted secret."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid


def cleanup_compose(command,env,temporary):
    try:
        subprocess.run(command+['down','--volumes','--remove-orphans'],env=env,capture_output=True,timeout=30,check=True)
    except Exception as error:
        error.add_note('Compose fixture retained at '+temporary.name)
        raise
    temporary.cleanup()


@unittest.skipUnless(os.environ.get('CONTROLLER_TEST_IMAGE') and os.environ.get('CONTROLLER_TEST_DOCKER_HOST'), 'controller image and Podman Compose socket not configured')
class ControllerComposeTests(unittest.TestCase):
    def test_private_secret_and_state_survive_stop_and_start(self):
        root=Path(__file__).resolve().parents[1]
        project='workflow-check-'+uuid.uuid4().hex[:12]
        temporary=tempfile.TemporaryDirectory(prefix='workflow-compose-',delete=False)
        with temporary as directory:
            secret=Path(directory)/'secret'
            image_file=Path(directory)/'image.yaml'
            env={**os.environ,'CONTROL_SECRET_PATH':str(secret),'DOCKER_HOST':os.environ['CONTROLLER_TEST_DOCKER_HOST']}
            command=['docker-compose','-p',project,'-f',str(root/'compose.yaml'),'-f',str(root/'compose.podman.yaml'),'-f',str(image_file)]
            def compose(*args):
                result=subprocess.run(command+list(args),env=env,capture_output=True,text=True,timeout=30)
                if result.returncode: self.fail('compose command failed: '+result.stderr)
                return result
            def request(container,action):
                code="import json,socket; s=socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect('/tmp/workflow-control/control.sock'); s.sendall(json.dumps({'secret':'x'*40,'request':"+repr(action)+"}).encode()+b'\\n'); print(s.makefile('rb').readline().decode())"
                result=subprocess.run(['podman','exec',container,'python','-c',code],capture_output=True,text=True,check=True,timeout=5)
                return json.loads(result.stdout)
            def wait_ready():
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    try:
                        container=compose('ps','-q','workflow').stdout.strip()
                        if not container: time.sleep(.2); continue
                        if request(container,{'action':'metrics'})['ok']: return container
                    except (subprocess.SubprocessError,ValueError,KeyError): time.sleep(.2)
                self.fail('controller socket unavailable')
            try:
                secret.write_text('x'*40); secret.chmod(0o600)
                image_file.write_text(json.dumps({'services':{'workflow':{'image':os.environ['CONTROLLER_TEST_IMAGE']}}}))
                compose('up','-d','--no-build')
                container=wait_ready()
                self.assertTrue(request(container,{'action':'create','run':'r','workflow':'marketing','task':'t'})['ok'])
                self.assertTrue(request(container,{'action':'enqueue','run':'r'})['ok'])
                expected_job={'ok':True,'result':{'job':{'status':'queued','worker':None,'expires':None}}}
                self.assertEqual(request(container,{'action':'job_status','run':'r'}),expected_job)
                compose('stop','workflow')
                compose('start','workflow')
                container=wait_ready()
                self.assertEqual(request(container,{'action':'job_status','run':'r'}),expected_job)
                compose('restart','--timeout','5','workflow')
                container=wait_ready()
                self.assertEqual(request(container,{'action':'job_status','run':'r'}),expected_job)
                compose('restart','workflow')
                container=wait_ready()
                self.assertEqual(request(container,{'action':'job_status','run':'r'}),expected_job)
                subprocess.run(['podman','kill','--signal','KILL',container],check=True,capture_output=True,timeout=10)
                compose('start','workflow')
                container=wait_ready()
                self.assertEqual(request(container,{'action':'job_status','run':'r'}),expected_job)
            finally:
                cleanup_compose(command,env,temporary)
