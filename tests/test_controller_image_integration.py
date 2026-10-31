"""Opt-in smoke test for a built controller image and fresh Podman volume."""
import json
import os
import subprocess
import time
import unittest
import uuid


@unittest.skipUnless(os.environ.get('CONTROLLER_TEST_IMAGE'), 'controller image not configured')
class ControllerImageTests(unittest.TestCase):
    def test_fresh_private_volume_persists_across_restart(self):
        image=os.environ['CONTROLLER_TEST_IMAGE']
        name='workflow-test-'+uuid.uuid4().hex
        volume=name+'-data'
        def podman(*args,**kwargs):
            return subprocess.run(['podman',*args],check=True,capture_output=True,text=True,**kwargs)
        def request(action):
            code="import json,socket; s=socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect('/data/control.sock'); s.sendall(json.dumps({'secret':'x'*40,'request':"+repr(action)+"}).encode()+b'\\n'); print(s.makefile('rb').readline().decode())"
            return json.loads(podman('exec',name,'python','-c',code,timeout=5).stdout)
        def start():
            podman('run','-d','--name',name,'--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs','/tmp','-v',volume+':/data','-e','CONTROL_SECRET_FILE=/data/secret','-e','WORKFLOW_DATA_DIR=/data',image)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                state=podman('inspect','--format','{{.State.Status}}',name).stdout.strip()
                if state in ('exited','dead'): self.fail('controller exited before socket became available')
                if state!='running': time.sleep(.2); continue
                try:
                    if request({'action':'metrics'})['ok']: return
                except (subprocess.SubprocessError,ValueError,KeyError): time.sleep(.2)
            self.fail('controller socket unavailable')
        podman('volume','create',volume)
        try:
            podman('run','--rm','--network=none','--user','0:0','-v',volume+':/data',image,'python','-c',"import os; p='/data/secret'; open(p,'w').write('x'*40); os.chown(p,10001,10001); os.chmod(p,0o600)")
            start()
            self.assertTrue(request({'action':'create','run':'r','workflow':'marketing','task':'t'})['ok'])
            duplicate=subprocess.run(['podman','run','--rm','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs','/tmp','-v',volume+':/data','-e','CONTROL_SECRET_FILE=/data/secret','-e','WORKFLOW_DATA_DIR=/data','-e','WORKFLOW_CONTROL_DIR=/tmp/other',image],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(duplicate.returncode,0)
            self.assertIn('state directory already in use',duplicate.stderr)
            podman('stop','--time','5',name); podman('rm',name)
            start()
            self.assertTrue(request({'action':'status','run':'r','stage':'intake'})['ok'])
        finally:
            subprocess.run(['podman','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['podman','volume','rm',volume],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
