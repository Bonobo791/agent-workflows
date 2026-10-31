"""Opt-in test of the authenticated local monitoring path with synthetic alerts."""
import base64
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

from runner.desktop_alert_receiver import build_server


@unittest.skipUnless(os.environ.get('WORKFLOW_LOCAL_MONITORING_TEST') == '1',
                     'local monitoring test not configured')
class LocalMonitoringIntegrationTests(unittest.TestCase):
    def test_worker_outage_fires_and_resolves_through_authenticated_stack(self):
        def port():
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                return sock.getsockname()[1]

        events = queue.Queue()
        receiver = build_server('z' * 40, lambda title, body: events.put((title, body)))
        thread = threading.Thread(target=receiver.serve_forever)
        thread.start()
        names = []
        worker = exporter = None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        auth = 'Basic ' + base64.b64encode(b'workflow:fixture-password').decode()
        prom_port, alert_port, metrics_port = port(), port(), port()

        def request(url, authorized=False):
            headers = {'Authorization': auth} if authorized else {}
            with opener.open(urllib.request.Request(url, headers=headers), timeout=3) as response:
                return response.read()

        temporary = tempfile.TemporaryDirectory(prefix='workflow-monitor-e2e-')
        try:
            directory = temporary.name
            root = Path(directory)
            data, config = root / 'data', root / 'config'
            data.mkdir(mode=0o700)
            config.mkdir(mode=0o700)
            prom_store, alert_store = root / 'prom-store', root / 'alert-store'
            prom_store.mkdir(mode=0o700)
            alert_store.mkdir(mode=0o700)
            for path, value in ((root / 'control', 'x' * 40),
                                (config / 'scrape-token', 'y' * 40),
                                (config / 'alert-token', 'z' * 40),
                                (config / 'alertmanager-password', 'fixture-password')):
                path.write_text(value)
                path.chmod(0o600)
            # Public fixture hash, never used as a real credential.
            (config / 'web.yml').write_text('basic_auth_users:\n  workflow: "$2b$04$abcdefghijklmnopqrstuuxDugxrpcpW3csMl3JkYMLtX7yxJF.2."\n')
            observability = Path(__file__).resolve().parents[1] / 'observability'
            rules = (observability / 'alerts.yml').read_text()
            (config / 'alerts.yml').write_text(rules.replace('for: 10m', 'for: 1s'))
            alert_config = (observability / 'local' / 'alertmanager.yml').read_text()
            alert_config = alert_config.replace('127.0.0.1:19192', f'127.0.0.1:{receiver.server_port}')
            alert_config = alert_config.replace('group_wait: 10s', 'group_wait: 0s').replace('group_interval: 5m', 'group_interval: 1s')
            (config / 'alertmanager.yml').write_text(alert_config)
            prom_config = (observability / 'local' / 'prometheus.yml').read_text()
            prom_config = prom_config.replace('30s', '1s').replace('127.0.0.1:19190', f'127.0.0.1:{metrics_port}')
            prom_config = prom_config.replace('127.0.0.1:19093', f'127.0.0.1:{alert_port}')
            (config / 'prometheus.yml').write_text(prom_config)
            env = {**os.environ, 'CONTROL_SECRET_FILE': str(root / 'control'),
                   'WORKFLOW_DATA_DIR': str(data), 'WORKFLOW_EXECUTION_MODE': 'disabled',
                   'WORKFLOW_METRICS_HTTP_SECRET_FILE': str(config / 'scrape-token'),
                   'WORKFLOW_METRICS_HTTP_PORT': str(metrics_port)}
            env.pop('WORKFLOW_CONTROL_DIR', None)
            def start_worker():
                process = subprocess.Popen([sys.executable, '-m', 'runner.worker'], env=env,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and process.poll() is None:
                    if (data / 'control.sock').is_socket():
                        return process
                    time.sleep(.05)
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                self.fail('worker did not start')
            def launch(suffix, image, *args, store):
                name = 'workflow-monitor-' + uuid.uuid4().hex + '-' + suffix
                names.append(name)
                subprocess.run(['podman', 'run', '-d', '--pull=never', '--name', name,
                                '--network=host', '--userns=keep-id', '--user',
                                f'{os.getuid()}:{os.getgid()}', '--read-only', '--cap-drop=ALL',
                                '--security-opt=no-new-privileges', '--tmpfs', '/tmp',
                                '-v', str(config) + ':/config:ro,z',
                                '-v', str(store) + ':/store:rw,Z', image, *args],
                               check=True, capture_output=True, timeout=30)
                return name
            def wait_up(expected):
                query = urllib.parse.urlencode({'query': 'up{job="workflow"}'})
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        result = json.loads(request(f'http://127.0.0.1:{prom_port}/api/v1/query?{query}', True))['data']['result']
                        if len(result) == 1 and result[0]['value'][1] == expected:
                            return
                    except urllib.error.HTTPError as error:
                        error.close()
                    except (OSError, KeyError, ValueError):
                        pass
                    time.sleep(.2)
                self.fail('Prometheus scrape did not reach ' + expected)
            def expect(resolved):
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    try:
                        title, body = events.get(timeout=1)
                    except queue.Empty:
                        continue
                    if 'metrics are unavailable' in body and ('resolved' in title) == resolved:
                        return
                self.fail('missing metrics alert ' + ('resolution' if resolved else 'firing'))
            worker = start_worker()
            exporter = subprocess.Popen([sys.executable, '-m', 'runner.metrics_http'], env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            alert_name = launch('alertmanager', 'docker.io/prom/alertmanager:v0.28.1',
                   '--config.file=/config/alertmanager.yml', '--storage.path=/store',
                   '--cluster.listen-address=', '--web.config.file=/config/web.yml',
                   f'--web.listen-address=127.0.0.1:{alert_port}', store=alert_store)
            prom_name = launch('prometheus', 'docker.io/prom/prometheus:v3.4.2',
                   '--config.file=/config/prometheus.yml', '--storage.tsdb.path=/store',
                   '--web.config.file=/config/web.yml', f'--web.listen-address=127.0.0.1:{prom_port}', store=prom_store)
            wait_up('1')
            for url in (f'http://127.0.0.1:{prom_port}/api/v1/status/config',
                        f'http://127.0.0.1:{alert_port}/api/v2/status'):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    request(url)
                self.assertEqual(caught.exception.code, 401)
                caught.exception.close()
                self.assertTrue(request(url, True))
            worker.terminate()
            self.assertEqual(worker.wait(timeout=10), 0)
            worker = None
            wait_up('0')
            expect(False)
            subprocess.run(['podman', 'stop', alert_name], check=True, capture_output=True, timeout=30)
            subprocess.run(['podman', 'start', alert_name], check=True, capture_output=True, timeout=30)
            subprocess.run(['podman', 'stop', prom_name], check=True, capture_output=True, timeout=30)
            subprocess.run(['podman', 'start', prom_name], check=True, capture_output=True, timeout=30)
            wait_up('0')
            query = urllib.parse.urlencode({'query': 'count_over_time(workflow_snapshot_timestamp_seconds[5m])'})
            history = json.loads(request(f'http://127.0.0.1:{prom_port}/api/v1/query?{query}', True))['data']['result']
            self.assertTrue(history, 'Prometheus did not retain the earlier snapshot after restart')
            worker = start_worker()
            wait_up('1')
            expect(True)
        finally:
            for name in reversed(names):
                subprocess.run(['podman', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30)
            for process in (exporter, worker):
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            receiver.shutdown()
            thread.join()
            receiver.server_close()
            temporary.cleanup()
