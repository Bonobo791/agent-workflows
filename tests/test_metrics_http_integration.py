"""Opt-in authenticated Prometheus scrape through a disposable worker."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.error
import urllib.request
import uuid


@unittest.skipUnless(os.environ.get('WORKFLOW_METRICS_HTTP_TEST')=='1','local Prometheus test not configured')
class MetricsHttpIntegrationTests(unittest.TestCase):
    def test_authenticated_scrape_recovers_after_worker_restart(self):
        def port():
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
        metric_port,prom_port=port(),port();name='workflow-metrics-test-'+uuid.uuid4().hex
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));worker=None;exporter=None
        with tempfile.TemporaryDirectory(prefix='workflow-metrics-http-') as directory:
            root=Path(directory);data=root/'data';data.mkdir(mode=0o700);config=root/'config';config.mkdir(mode=0o700)
            control=root/'control-secret';control.write_text('x'*40);control.chmod(0o600)
            scrape=config/'scrape-secret';scrape.write_text('y'*40);scrape.chmod(0o600)
            (config/'prometheus.yml').write_text(f"global:\n  scrape_interval: 1s\n  evaluation_interval: 1s\nscrape_configs:\n  - job_name: workflow\n    authorization:\n      credentials_file: /config/scrape-secret\n    static_configs:\n      - targets: ['127.0.0.1:{metric_port}']\n")
            env={**os.environ,'CONTROL_SECRET_FILE':str(control),'WORKFLOW_DATA_DIR':str(data),'WORKFLOW_EXECUTION_MODE':'disabled','WORKFLOW_METRICS_HTTP_SECRET_FILE':str(scrape),'WORKFLOW_METRICS_HTTP_PORT':str(metric_port)}
            env.pop('WORKFLOW_CONTROL_DIR',None)
            def start_worker():
                process=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                deadline=time.monotonic()+10
                while not (data/'control.sock').is_socket() and process.poll() is None and time.monotonic()<deadline:time.sleep(.05)
                if not (data/'control.sock').is_socket():
                    if process.poll() is None:process.terminate();process.wait(timeout=10)
                    self.fail('worker control socket unavailable')
                return process
            def wait_up(expected):
                query=urllib.parse.urlencode({'query':'up{job="workflow"}'})
                deadline=time.monotonic()+25
                while time.monotonic()<deadline:
                    try:
                        with opener.open(f'http://127.0.0.1:{prom_port}/api/v1/query?'+query,timeout=2) as response:result=json.load(response)['data']['result']
                        if len(result)==1 and result[0]['value'][1]==expected:return
                    except urllib.error.HTTPError as error:error.close()
                    except (OSError,KeyError,ValueError):pass
                    time.sleep(.2)
                self.fail('Prometheus did not observe expected scrape state '+expected)
            try:
                worker=start_worker()
                exporter=subprocess.Popen([sys.executable,'-m','runner.metrics_http'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                subprocess.run(['podman','run','-d','--pull=never','--name',name,'--network=host','--userns=keep-id','--user',f'{os.getuid()}:{os.getgid()}','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs','/tmp','-v',str(config)+':/config:ro,Z','docker.io/prom/prometheus:v3.4.2','--config.file=/config/prometheus.yml','--storage.tsdb.path=/tmp/prometheus',f'--web.listen-address=127.0.0.1:{prom_port}'],check=True,capture_output=True,timeout=30)
                wait_up('1')
                worker.terminate();self.assertEqual(worker.wait(timeout=10),0);worker=None
                wait_up('0')
                worker=start_worker();wait_up('1')
            finally:
                subprocess.run(['podman','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
                for process in (exporter,worker):
                    if process is not None and process.poll() is None:
                        process.terminate();process.wait(timeout=10)
