import http.server
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from runner.audit_delivery import deliver_once
from runner.graph import GraphStore, engineering_definition
from runner.opik_export import PATH

try:
    import requests
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:
    requests=None; OTLPSpanExporter=None


@unittest.skipUnless(requests is not None and OTLPSpanExporter is not None,'optional audit export dependencies unavailable')
class AuditDeliveryTests(unittest.TestCase):
    def test_private_files_and_unsafe_configuration_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            state=Path(root)/'state.db'; state.write_bytes(b''); state.chmod(0o600)
            key=Path(root)/'key'; key.write_text('x'*40); key.chmod(0o600)
            cursor=Path(root)/'cursor.db'
            with self.assertRaises(ValueError): deliver_once(state,cursor,key,'http://example.com'+PATH,'graph_audit')
            with self.assertRaises(ValueError): deliver_once(state,cursor,key,'http://localhost'+PATH,'not_a_table')
            state.chmod(0o644)
            with self.assertRaises(ValueError): deliver_once(state,cursor,key,'http://localhost'+PATH,'graph_audit')
            state.chmod(0o600); os.link(state,Path(root)/'hardlink')
            with self.assertRaises(ValueError): deliver_once(state,cursor,key,'http://localhost'+PATH,'graph_audit')
            (Path(root)/'hardlink').unlink(); state.unlink(); state.symlink_to('/dev/null')
            with self.assertRaises(OSError): deliver_once(state,cursor,key,'http://localhost'+PATH,'graph_audit')

    def test_loopback_delivery_retries_and_reopens_private_cursor(self):
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest, ExportTraceServiceResponse
        class Receiver(http.server.BaseHTTPRequestHandler):
            payloads=[]; reject=True
            def log_message(self,*args): pass
            def do_POST(self):
                Receiver.payloads.append((self.path,self.headers.get('Authorization'),self.rfile.read(int(self.headers['Content-Length']))))
                body=ExportTraceServiceResponse(partial_success={'rejected_spans':1} if Receiver.reject else {}).SerializeToString()
                self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Receiver); thread=threading.Thread(target=server.serve_forever); thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                graph=GraphStore(root+'/state.db'); graph.register(engineering_definition()); graph.create('private-run','engineering'); claim=graph.claim('private-run',60)
                graph.finish('private-run','intake',graph.prepare('private-run','intake',claim),'passed','private-result',claim); graph.close(); Path(root+'/state.db').chmod(0o600)
                key=Path(root)/'key'; key.write_text('x'*40); key.chmod(0o600)
                token=Path(root)/'token'; token.write_text('y'*40); token.chmod(0o600)
                cursor=Path(root)/'cursor.db'; endpoint=f'http://127.0.0.1:{server.server_port}{PATH}'
                self.assertEqual(deliver_once(root+'/state.db',cursor,key,endpoint,'graph_audit',token_file=token)['status'],'unavailable')
                Receiver.reject=False
                command=[sys.executable,'-m','runner.audit_delivery','--source',root+'/state.db','--cursor',str(cursor),
                         '--key-file',str(key),'--endpoint',endpoint,'--table','graph_audit','--token-file',str(token)]
                result=subprocess.run(command,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(result.stdout.strip(),'exported')
                self.assertEqual(deliver_once(root+'/state.db',cursor,key,endpoint,'graph_audit',token_file=token)['status'],'empty')
                self.assertEqual(len(Receiver.payloads),2)
                for path,authorization,payload in Receiver.payloads:
                    self.assertEqual(path,PATH); self.assertEqual(authorization,'Bearer '+'y'*40)
                    self.assertNotIn(b'private-run',payload); self.assertNotIn(b'private-result',payload)
                    self.assertTrue(ExportTraceServiceRequest.FromString(payload).resource_spans)
                self.assertEqual(os.stat(cursor).st_mode&0o777,0o600)
                with sqlite3.connect(cursor) as db: self.assertEqual(db.execute('SELECT position FROM export_cursors').fetchone(),(1,))
        finally: server.shutdown(); thread.join(); server.server_close()
