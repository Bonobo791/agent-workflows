"""Opt-in local authenticated collector and private file export probe."""
from contextlib import closing
import os
import shlex
import shutil
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid

from runner.service import Service
from runner.opik_export import PATH
from tests.monitoring_cleanup import cleanup_monitoring


@unittest.skipUnless(os.environ.get('WORKFLOW_TEST_OTEL_CONTRIB_IMAGE'),'collector contrib image not configured')
class AuditCollectorAuthIntegrationTests(unittest.TestCase):
    def test_authenticated_private_output_survives_restart_and_retry(self):
        systemd=os.environ.get('WORKFLOW_TEST_AUDIT_SYSTEMD')=='1'
        temporary=tempfile.TemporaryDirectory(prefix='workflow-otel-auth-',dir=os.environ.get('XDG_RUNTIME_DIR') if systemd else None,delete=False)
        root=Path(temporary.name); name='workflow-otel-auth-'+uuid.uuid4().hex
        def podman(*args): return subprocess.run(['podman',*args],check=True,capture_output=True,text=True,timeout=30).stdout
        try:
            config=root/'config'; config.mkdir(mode=0o700)
            (config/'collector.yml').write_bytes((Path(__file__).resolve().parents[1]/'observability/local/audit-collector.yml').read_bytes()); (config/'collector.yml').chmod(0o600)
            (config/'token').write_text('y'*40); (config/'token').chmod(0o600)
            store=root/'store'; store.mkdir(mode=0o700)
            podman('run','-d','--pull=never','--name',name,'--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs','/tmp','--userns=keep-id','--user',f'{os.getuid()}:{os.getgid()}','-p','127.0.0.1::4318','-v',str(config)+':/config:ro,Z','-v',str(store)+':/store:rw,Z',os.environ['WORKFLOW_TEST_OTEL_CONTRIB_IMAGE'],'--config=/config/collector.yml')
            host,port=podman('port',name,'4318/tcp').strip().rsplit(':',1); self.assertEqual(host,'127.0.0.1'); port=int(port)
            endpoint=f'http://127.0.0.1:{port}{PATH}'
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def ready():
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    try:
                        with opener.open(urllib.request.Request(endpoint,data=b'',method='POST'),timeout=.5): pass
                    except urllib.error.HTTPError as error:
                        with error:
                            if error.code==401: return
                    except OSError: pass
                    if podman('inspect','--format','{{.State.Running}}',name).strip()!='true':
                        logs=subprocess.run(['podman','logs',name],capture_output=True,text=True,timeout=10)
                        self.fail('collector exited: '+logs.stderr[-1500:])
                    time.sleep(.1)
                self.fail('collector did not become ready')
            ready()
            for headers in ({},{'Authorization':'Bearer '+'z'*40}):
                request=urllib.request.Request(endpoint,data=b'',headers=headers,method='POST')
                with self.assertRaises(urllib.error.HTTPError) as denied: opener.open(request,timeout=3)
                self.assertEqual(denied.exception.code,401); denied.exception.close()
            service=Service(root); graph=service.graph; graph.create('private-run','engineering'); claim=graph.claim('private-run',60)
            graph.finish('private-run','intake',graph.prepare('private-run','intake',claim),'passed','private-content',claim)
            (root/'state.db').chmod(0o600); (root/'key').write_text('x'*40); (root/'key').chmod(0o600)
            command=[sys.executable,'-m','runner.audit_delivery','--source',str(root/'state.db'),'--cursor',str(root/'cursor.db'),'--key-file',str(root/'key'),'--endpoint',endpoint,'--table','graph_audit','--token-file',str(config/'token')]
            wrong=root/'wrong-token'; wrong.write_text('z'*40); wrong.chmod(0o600)
            denied=subprocess.run(command[:-1]+[str(wrong)],capture_output=True,text=True,timeout=20)
            self.assertNotEqual(denied.returncode,0); self.assertEqual(denied.stdout.strip(),'unavailable')
            with closing(sqlite3.connect(root/'cursor.db')) as db: self.assertEqual(db.execute('SELECT position FROM export_cursors').fetchone(),(0,))
            first=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertEqual((first.returncode,first.stdout.strip()),(0,'exported'),first.stderr)
            graph.finish('private-run','plan',graph.prepare('private-run','plan',claim),'passed','private-content',claim); service.close()
            output=store/'audit.json'; before=output.read_bytes(); self.assertIn(b'workflow.audit.snapshot',before)
            self.assertNotIn(b'private-run',before); self.assertNotIn(b'private-content',before); self.assertNotIn(b'y'*40,before)
            podman('stop','--time','5',name)
            failed=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertNotEqual(failed.returncode,0); self.assertEqual(failed.stdout.strip(),'unavailable')
            podman('start',name); ready()
            second=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertEqual((second.returncode,second.stdout.strip()),(0,'exported'),second.stderr)
            after=output.read_bytes(); self.assertTrue(after.startswith(before)); self.assertGreater(len(after),len(before))
            again=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertEqual((again.returncode,again.stdout.strip()),(0,'empty'))
            if systemd:
                unit=Path(__file__).resolve().parents[1]/'observability/systemd/agent-workflows-audit-delivery.service'
                lines=unit.read_text().splitlines(); start=next(line[10:] for line in lines if line.startswith('ExecStart='))
                start=start.replace('http://127.0.0.1:14318'+PATH,endpoint).replace('%h/.config/agent-workflows/audit-collector/token',str(config/'token'))
                app=root/'app'; app.mkdir(mode=0o700)
                for package in ('runner','workflows'): shutil.copytree(Path(__file__).resolve().parents[1]/package,app/package,ignore=shutil.ignore_patterns('__pycache__'))
                env_file=root/'audit.env'
                env_file.write_text('PYTHONPATH='+str(app)+'\nWORKFLOW_AUDIT_SOURCE='+str(root/'state.db')+'\nWORKFLOW_AUDIT_CURSOR='+str(root/'cursor.db')+'\nWORKFLOW_AUDIT_KEY_FILE='+str(root/'key')+'\n'); env_file.chmod(0o600)
                props=['--property='+line for line in lines if line.startswith(('UMask=','NoNewPrivileges=','PrivateTmp=','ProtectSystem=','TimeoutStartSec='))]
                unit_name='workflow-audit-delivery-'+uuid.uuid4().hex
                try:
                    invocation=subprocess.run(['systemd-run','--user','--wait','--collect','--pipe','--quiet','--unit='+unit_name,'--property=EnvironmentFile='+str(env_file),*props,*shlex.split(start)],capture_output=True,text=True,timeout=60)
                    self.assertEqual(invocation.returncode,0,invocation.stdout+invocation.stderr)
                except BaseException:
                    subprocess.run(['systemctl','--user','stop',unit_name],capture_output=True,timeout=15)
                    raise
                with closing(sqlite3.connect(root/'cursor.db')) as db:
                    self.assertEqual(db.execute("SELECT position FROM export_cursors WHERE table_name='claim_audit'").fetchone(),(1,))
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
            batch=ExportTraceServiceRequest(); spans=batch.resource_spans.add().scope_spans.add().spans
            for i in range(250):
                span=spans.add(); span.trace_id=b'a'*16; span.span_id=(i+1).to_bytes(8,'big'); span.name='synthetic-'+('a'*2500)
            payload=batch.SerializeToString()
            headers={'Authorization':'Bearer '+'y'*40,'Content-Type':'application/x-protobuf'}
            for _ in range(24):
                request=urllib.request.Request(endpoint,data=payload,headers=headers,method='POST')
                with opener.open(request,timeout=10) as response: self.assertEqual(response.status,200)
            self.assertTrue(list(store.glob('audit-*-size.json')),'size rotation did not occur')
        finally: cleanup_monitoring([name],(),None,None,temporary)
