"""Opt-in local worker unit startup with a pinned signed-in runtime."""
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from runner.local_process import start_local_process
from runner.local_scope import SystemdScopes
from runner.protocol import AppServer
from runner.service import Service
from tests.test_local_executor_crash_live import cleanup_crash_fixture


@unittest.skipUnless(os.environ.get('CODEX_TEST_EXECUTABLE') and os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','signed-in local runtime not configured')
class LocalWorkerSystemdLiveTests(unittest.TestCase):
    def test_local_worker_unit_starts_and_preserves_queue_across_restart(self):
        temporary=tempfile.TemporaryDirectory(prefix='workflow-local-unit-',dir=os.environ.get('XDG_RUNTIME_DIR'),delete=False)
        root=Path(temporary.name); name='workflow-local-unit-'+uuid.uuid4().hex; backend=SystemdScopes(dict(os.environ))
        started=False
        try:
            runtime=root/'runtime.json'
            result=subprocess.run([sys.executable,'-m','runner.local_config_inspect',os.environ['CODEX_TEST_EXECUTABLE'],str(root),'--write',str(runtime)],capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(result.stdout.strip(),'Local runtime configuration created.')
            self.assertEqual(runtime.stat().st_mode&0o777,0o600); config=json.loads(runtime.read_text())
            service=Service(root); service.close()
            discovery=start_local_process(str(root/'state.db'),'discovery',str(root),config['codex_executable'],os.environ,backend,lambda:True,config['disabled_integrations'])
            try:
                app=AppServer(discovery.open_transport(time.monotonic()+90,lambda:False)); app.initialize()
                self.assertTrue(discovery.verify_config(app._request))
                self.assertIsInstance(app._request('account/read',{'refreshToken':False}).get('account'),dict)
            finally: discovery.finish()
            secret=root/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            env_file=root/'worker.env'
            values={'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'WORKFLOW_DATA_DIR':str(root),'WORKFLOW_CONTROL_DIR':str(root),
                    'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_EXECUTION_MODE':'local','LOCAL_RUNTIME_CONFIG_FILE':str(runtime),
                    'WORKFLOW_LIFECYCLE_MODE':'manual','MANUAL_WORKER_TARGET':'fixture','MANUAL_WORKER_INCARNATION':'one'}
            if os.environ.get('CODEX_HOME'): values['CODEX_HOME']=os.environ['CODEX_HOME']
            env_file.write_text(''.join(key+'='+value+'\n' for key,value in values.items())); env_file.chmod(0o600)
            unit=Path(__file__).resolve().parents[1]/'deploy/systemd/agent-workflows-local.service'; lines=unit.read_text().splitlines()
            command=shlex.split(next(line[10:] for line in lines if line.startswith('ExecStart=')))
            props=['--property='+line for line in lines if line.startswith(('UMask=','NoNewPrivileges=','KillMode=','TimeoutStopSec=','Restart='))]
            def start():
                subprocess.run(['systemd-run','--user','--unit='+name,'--property=EnvironmentFile='+str(env_file),*props,*command],check=True,capture_output=True,timeout=30)
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if (root/'control.sock').is_socket(): return
                    time.sleep(.05)
                self.fail('local worker control socket unavailable')
            def request(action):
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                    client.settimeout(5); client.connect(str(root/'control.sock'))
                    client.sendall(json.dumps({'secret':'x'*40,'request':action}).encode()+b'\n')
                    with client.makefile('rb') as stream: return json.loads(stream.readline())
            started=True; start()
            self.assertTrue(request({'action':'create','run':'r','workflow':'marketing','task':'synthetic'})['ok'])
            self.assertTrue(request({'action':'enqueue','run':'r'})['ok'])
            self.assertEqual(request({'action':'job_status','run':'r'})['result']['job']['status'],'queued')
            subprocess.run(['systemctl','--user','stop',name],check=True,capture_output=True,timeout=30); started=False
            self.assertFalse((root/'control.sock').exists())
            started=True; start()
            self.assertEqual(request({'action':'job_status','run':'r'})['result']['job']['status'],'queued')
        finally:
            if started:
                try: subprocess.run(['systemctl','--user','stop',name],check=True,capture_output=True,timeout=30)
                except Exception as error:
                    error.add_note('Local worker fixture retained at '+str(root)); raise
            cleanup_crash_fixture(temporary,backend)
