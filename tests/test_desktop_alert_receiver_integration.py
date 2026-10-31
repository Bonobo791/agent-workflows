"""Opt-in Alertmanager delivery to a synthetic desktop notification callback."""
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid

from runner.desktop_alert_receiver import build_server
from tests.monitoring_cleanup import cleanup_monitoring


@unittest.skipUnless(os.environ.get('WORKFLOW_DESKTOP_ALERT_TEST')=='1','local Alertmanager test not configured')
class DesktopAlertIntegrationTests(unittest.TestCase):
    def test_alertmanager_authenticates_and_delivers_firing_and_resolution(self):
        events=[];server=build_server('x'*40,lambda title,body:events.append((title,body)),port=0)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        name='workflow-desktop-test-'+uuid.uuid4().hex
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        temporary=tempfile.TemporaryDirectory(prefix='workflow-desktop-alert-',delete=False)
        try:
            root=Path(temporary.name);(root/'secret').write_text('x'*40);(root/'secret').chmod(0o600)
            config=("route:\n  receiver: local\n  group_wait: 0s\n  group_interval: 1s\n  repeat_interval: 1h\n"
                    "receivers:\n  - name: local\n    webhook_configs:\n"
                    f"      - url: http://127.0.0.1:{server.server_port}/alerts\n"
                    "        send_resolved: true\n        http_config:\n          authorization:\n            credentials_file: /config/secret\n")
            (root/'alertmanager.yml').write_text(config);(root/'alertmanager.yml').chmod(0o600)
            subprocess.run(['podman','run','-d','--pull=never','--name',name,'--network=host','--userns=keep-id','--user',f'{os.getuid()}:{os.getgid()}','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs','/tmp','-v',str(root)+':/config:ro,Z','docker.io/prom/alertmanager:v0.28.1','--config.file=/config/alertmanager.yml','--storage.path=/tmp/alertmanager','--cluster.listen-address=',f'--web.listen-address=127.0.0.1:{port}'],check=True,capture_output=True,timeout=30)
            base=f'http://127.0.0.1:{port}';deadline=time.monotonic()+15
            while True:
                try:
                    with opener.open(base+'/-/ready',timeout=1) as response:
                        if response.status==200:break
                except OSError:
                    if time.monotonic()>=deadline:self.fail('Alertmanager did not become ready')
                    time.sleep(.1)
            now=datetime.datetime.now(datetime.timezone.utc)
            start=(now-datetime.timedelta(minutes=1)).isoformat().replace('+00:00','Z')
            def post(end):
                data=json.dumps([{'labels':{'alertname':'WorkflowExpiredLease','severity':'warning'},'annotations':{'summary':'private fixture'},'startsAt':start,'endsAt':end.isoformat().replace('+00:00','Z')}]).encode()
                request=urllib.request.Request(base+'/api/v2/alerts',data=data,headers={'Content-Type':'application/json'},method='POST')
                with opener.open(request,timeout=3) as response:self.assertEqual(response.status,200)
            def wait(count):
                deadline=time.monotonic()+20
                while len(events)<count and time.monotonic()<deadline:time.sleep(.05)
                self.assertGreaterEqual(len(events),count)
            post(now+datetime.timedelta(minutes=2));wait(1)
            post(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=1));wait(2)
            self.assertIn('expired',str(events[0]).lower());self.assertIn('resolved',str(events[-1]).lower())
            self.assertNotIn('private fixture',str(events))
        finally:
            cleanup_monitoring([name], (), server, thread, temporary)
