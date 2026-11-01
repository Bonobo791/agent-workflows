"""Opt-in audit delivery to a disposable real OpenTelemetry Collector."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from runner.graph import GraphStore, engineering_definition
from runner.opik_export import PATH
from tests.monitoring_cleanup import cleanup_monitoring


@unittest.skipUnless(os.environ.get('WORKFLOW_TEST_OTEL_IMAGE'),'collector image not configured')
class AuditCollectorIntegrationTests(unittest.TestCase):
    def test_unavailable_collector_replays_private_events_after_restart(self):
        temporary=tempfile.TemporaryDirectory(prefix='workflow-otel-',delete=False)
        root=Path(temporary.name); name='workflow-otel-'+uuid.uuid4().hex
        def podman(*args):
            return subprocess.run(['podman',*args],check=True,capture_output=True,text=True,timeout=30).stdout
        try:
            config_dir=root/'config'; config_dir.mkdir(mode=0o700)
            config=config_dir/'collector.yaml'
            config.write_text('receivers:\n  otlp:\n    protocols:\n      http:\n        endpoint: 0.0.0.0:4318\n        traces_url_path: '+PATH+'\nexporters:\n  debug:\n    verbosity: detailed\nservice:\n  pipelines:\n    traces:\n      receivers: [otlp]\n      exporters: [debug]\n')
            config.chmod(0o600)
            podman('run','-d','--pull=never','--name',name,'--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs','/tmp','--userns=keep-id','--user',f'{os.getuid()}:{os.getgid()}','-p','127.0.0.1::4318','-v',str(config_dir)+':/config:ro,Z',os.environ['WORKFLOW_TEST_OTEL_IMAGE'],'--config=/config/collector.yaml')
            address=podman('port',name,'4318/tcp').strip(); host,port=address.rsplit(':',1)
            self.assertEqual(host,'127.0.0.1'); port=int(port)
            def ready():
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    try:
                        with socket.create_connection((host,port),timeout=.5): return
                    except OSError: time.sleep(.1)
                self.fail('collector did not become ready')
            ready()
            graph=GraphStore(str(root/'state.db')); graph.register(engineering_definition()); graph.create('private-run','engineering'); claim=graph.claim('private-run',60)
            graph.finish('private-run','intake',graph.prepare('private-run','intake',claim),'passed','private-content',claim); graph.close(); (root/'state.db').chmod(0o600)
            (root/'key').write_text('x'*40); (root/'key').chmod(0o600)
            command=[sys.executable,'-m','runner.audit_delivery','--source',str(root/'state.db'),'--cursor',str(root/'cursor.db'),'--key-file',str(root/'key'),'--endpoint',f'http://127.0.0.1:{port}{PATH}','--table','graph_audit']
            podman('stop','--time','5',name)
            failed=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertNotEqual(failed.returncode,0); self.assertEqual(failed.stdout.strip(),'unavailable')
            podman('start',name); ready()
            delivered=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertEqual(delivered.returncode,0,delivered.stderr); self.assertEqual(delivered.stdout.strip(),'exported')
            repeat=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertEqual((repeat.returncode,repeat.stdout.strip()),(0,'empty'))
            recorded=subprocess.run(['podman','logs',name],check=True,capture_output=True,text=True,timeout=30)
            logs=recorded.stdout+recorded.stderr
            self.assertIn('workflow.audit.snapshot',logs); self.assertNotIn('private-run',logs); self.assertNotIn('private-content',logs)
        finally: cleanup_monitoring([name],(),None,None,temporary)
