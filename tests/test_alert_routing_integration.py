"""Opt-in disposable Prometheus/Alertmanager routing check; no real receivers."""
import http.server
import json
import os
from pathlib import Path
import queue
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid


@unittest.skipUnless(os.environ.get('WORKFLOW_ALERT_ROUTING_TEST')=='1','local alert routing test not configured')
class AlertRoutingTests(unittest.TestCase):
    def test_uncertain_job_fires_and_resolves_through_local_webhook(self):
        from runner.metrics_textfile import publish
        from runner.service import Service
        notifications=queue.Queue()
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                notifications.put(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.end_headers()
            def log_message(self,*args): pass
        def port():
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]
        def podman(*args):
            return subprocess.run(['podman',*args],check=True,capture_output=True,text=True,timeout=30)
        server=http.server.HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever); thread.start()
        names=[]; worker=None
        try:
            with tempfile.TemporaryDirectory(prefix='workflow-alert-') as root:
                root=Path(root); data=root/'data'; data.mkdir(mode=0o700)
                metrics=root/'metrics'; metrics.mkdir(mode=0o700)
                config=root/'config'; config.mkdir(mode=0o700)
                secret=root/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
                service=Service(data)
                try:
                    service.create('r','marketing','t'); service.enqueue('r')
                    service.graph.db.execute("UPDATE jobs SET status='uncertain' WHERE run='r'")
                finally: service.close()
                env={**os.environ,'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_DATA_DIR':str(data),'WORKFLOW_EXECUTION_MODE':'disabled'}
                env.pop('WORKFLOW_CONTROL_DIR',None)
                worker=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    deadline=time.monotonic()+10
                    while not (data/'control.sock').exists() and worker.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                    self.assertTrue((data/'control.sock').is_socket())
                    from unittest.mock import patch
                    with patch.dict(os.environ,env,clear=True): publish(str(metrics))
                    nodeport,amport,promport=port(),port(),port()
                    rules=(Path(__file__).resolve().parents[1]/'observability'/'alerts.yml').read_text()
                    (config/'rules.yml').write_text(rules.replace('for: 5m','for: 1s'))
                    (config/'alertmanager.yml').write_text(f"route:\n  receiver: local\n  group_wait: 0s\n  group_interval: 1s\n  repeat_interval: 1h\nreceivers:\n  - name: local\n    webhook_configs:\n      - url: http://127.0.0.1:{server.server_port}/alerts\n        send_resolved: true\n")
                    (config/'prometheus.yml').write_text(f"global:\n  scrape_interval: 1s\n  evaluation_interval: 1s\nrule_files:\n  - /config/rules.yml\nscrape_configs:\n  - job_name: workflow\n    static_configs:\n      - targets: ['127.0.0.1:{nodeport}']\nalerting:\n  alertmanagers:\n    - static_configs:\n        - targets: ['127.0.0.1:{amport}']\n")
                    prefix='workflow-alert-'+uuid.uuid4().hex[:12]
                    def launch(suffix,image,*args,volume):
                        name=prefix+'-'+suffix; names.append(name)
                        podman('run','-d','--pull=never','--name',name,'--network=host','--userns=keep-id','--user',f'{os.getuid()}:{os.getgid()}','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs','/tmp','-v',volume,image,*args)
                    launch('node','quay.io/prometheus/node-exporter:v1.9.1','--collector.disable-defaults','--collector.textfile','--collector.textfile.directory=/collector',f'--web.listen-address=127.0.0.1:{nodeport}',volume=str(metrics)+':/collector:ro,Z')
                    launch('alertmanager','docker.io/prom/alertmanager:v0.28.1','--config.file=/config/alertmanager.yml','--storage.path=/tmp/alertmanager','--cluster.listen-address=',f'--web.listen-address=127.0.0.1:{amport}',volume=str(config)+':/config:ro,Z')
                    launch('prometheus','docker.io/prom/prometheus:v3.4.2','--config.file=/config/prometheus.yml','--storage.tsdb.path=/tmp/prometheus',f'--web.listen-address=127.0.0.1:{promport}',volume=str(config)+':/config:ro,Z')
                    def expect(status):
                        deadline=time.monotonic()+70
                        while time.monotonic()<deadline:
                            try: payload=notifications.get(timeout=min(2,max(.01,deadline-time.monotonic())))
                            except queue.Empty: continue
                            for alert in payload.get('alerts',[]):
                                if alert.get('labels',{}).get('alertname')=='WorkflowUncertainExecution' and alert.get('status')==status:
                                    self.assertNotIn('x'*40,json.dumps(payload)); return
                        self.fail('missing '+status+' notification')
                    expect('firing')
                    with sqlite3.connect(data/'state.db') as db: db.execute("UPDATE jobs SET status='completed' WHERE run='r'")
                    with patch.dict(os.environ,env,clear=True): publish(str(metrics))
                    expect('resolved')
                finally:
                    for name in reversed(names): subprocess.run(['podman','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    if worker is not None: worker.terminate(); worker.communicate(timeout=10)
        finally:
            server.shutdown(); thread.join(); server.server_close()
