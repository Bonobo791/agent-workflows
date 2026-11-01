import http.server
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from runner.audit_delivery import deliver_once, deliver_tables, present_tables
from runner.graph import GraphStore, engineering_definition
from runner.opik_export import PATH

try:
    import requests
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:
    requests=None; OTLPSpanExporter=None


@unittest.skipUnless(requests is not None and OTLPSpanExporter is not None,'optional audit export dependencies unavailable')
class AuditDeliveryTests(unittest.TestCase):
    def test_all_present_requires_core_tables_and_selects_only_existing_audit_tables(self):
        from runner.service import Service
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                selected=present_tables(root+'/state.db')
                self.assertTrue({'graph_audit','claim_audit','job_audit','action_audit','attempt_audit'}<=set(selected))
                self.assertNotIn('local_scope_audit',selected)
                service.graph.db.execute('DROP TABLE claim_audit')
                with self.assertRaises(ValueError): present_tables(root+'/state.db')
            finally: service.close()

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

    def test_explicit_table_batch_rejects_duplicates_and_missing_tables_before_send(self):
        from unittest.mock import Mock, patch
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/state.db'); graph.register(engineering_definition()); graph.create('r','engineering'); graph.claim('r',60); graph.close(); Path(root+'/state.db').chmod(0o600)
            key=Path(root)/'key'; key.write_text('x'*40); key.chmod(0o600)
            adapter=Mock(); adapter.return_value=True
            with patch('runner.audit_delivery.create_opik_trace_exporter',return_value=adapter):
                for tables in (['claim_audit','claim_audit'],['claim_audit','job_audit']):
                    with self.subTest(tables=tables), self.assertRaises(ValueError):
                        deliver_tables(root+'/state.db',root+'/cursor.db',key,'http://127.0.0.1'+PATH,tables)
                adapter.assert_not_called()
                result=deliver_tables(root+'/state.db',root+'/cursor.db',key,'http://127.0.0.1'+PATH,['claim_audit','graph_audit'])
                self.assertEqual(result,{'claim_audit':{'status':'exported','cursor':1},'graph_audit':{'status':'empty','cursor':0}})
                self.assertEqual(adapter.call_count,1)

    def test_batch_stops_on_failed_delivery_and_resumes_per_table(self):
        from unittest.mock import Mock, patch
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/state.db'); graph.register(engineering_definition()); graph.create('r','engineering'); claim=graph.claim('r',60)
            graph.finish('r','intake',graph.prepare('r','intake',claim),'passed','result',claim); graph.close(); Path(root+'/state.db').chmod(0o600)
            key=Path(root)/'key'; key.write_text('x'*40); key.chmod(0o600)
            adapter=Mock(side_effect=[True,False,True])
            args=(root+'/state.db',root+'/cursor.db',key,'http://127.0.0.1'+PATH,['claim_audit','graph_audit'])
            with patch('runner.audit_delivery.create_opik_trace_exporter',return_value=adapter):
                result=deliver_tables(*args)
                self.assertEqual([result[t]['status'] for t in args[-1]],['exported','unavailable'])
                result=deliver_tables(*args)
                self.assertEqual([result[t]['status'] for t in args[-1]],['empty','exported'])
                self.assertEqual(adapter.call_count,3)

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
                from runner.service import Service
                service=Service(root); graph=service.graph; graph.create('private-run','engineering'); claim=graph.claim('private-run',60)
                graph.finish('private-run','intake',graph.prepare('private-run','intake',claim),'passed','private-result',claim); service.close()
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
                batch=subprocess.run(command+['--table','claim_audit'],capture_output=True,text=True,timeout=15)
                self.assertEqual(batch.returncode,0,batch.stderr)
                self.assertEqual({k:v['status'] for k,v in json.loads(batch.stdout).items()},{'graph_audit':'empty','claim_audit':'exported'})
                all_command=command[:command.index('--table')]+['--all-present','--token-file',str(token)]
                complete=subprocess.run(all_command,capture_output=True,text=True,timeout=15)
                self.assertEqual(complete.returncode,0,complete.stderr)
                all_results=json.loads(complete.stdout)
                self.assertTrue({'claim_audit','graph_audit','job_audit','action_audit','attempt_audit'}<=set(all_results))
                self.assertTrue(all(item['status']=='empty' for item in all_results.values()))
                self.assertEqual(len(Receiver.payloads),3)
                for path,authorization,payload in Receiver.payloads:
                    self.assertEqual(path,PATH); self.assertEqual(authorization,'Bearer '+'y'*40)
                    self.assertNotIn(b'private-run',payload); self.assertNotIn(b'private-result',payload)
                    self.assertTrue(ExportTraceServiceRequest.FromString(payload).resource_spans)
                self.assertEqual(os.stat(cursor).st_mode&0o777,0o600)
                with closing(sqlite3.connect(cursor)) as db: self.assertEqual(dict(db.execute('SELECT table_name,position FROM export_cursors'))['graph_audit'],1)
        finally: server.shutdown(); thread.join(); server.server_close()
