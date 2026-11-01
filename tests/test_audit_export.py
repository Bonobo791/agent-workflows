import unittest
import tempfile
try:
    from opentelemetry.sdk._logs.export import LogRecordExportResult
except ImportError:
    LogRecordExportResult = None
try:
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
except ImportError:
    OTLPLogExporter = None
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:
    OTLPSpanExporter = None
from runner.audit_export import project_event, read_events, export_page, deliver_page
from runner.graph import GraphStore, engineering_definition
from runner.otel_audit import OpenTelemetryAuditExporter, OpenTelemetryAuditTraceExporter, StrictOTLPTraceSession


class AuditExportTests(unittest.TestCase):
    def test_launch_receipt_projection_correlates_without_private_identity(self):
        from runner.otel_audit import _attributes
        key=b'x'*32
        row={'id':1,'schema_version':1,'run':'private-run','unit':'private-unit',
             'invocation':'private-invocation','cgroup':'private-cgroup','pid':123,
             'nonce_hash':'private-nonce','status':'bound','created':1}
        event=project_event('local_scope_launch_audit',row,key)
        scope=project_event('local_scope_audit',{'id':2,'schema_version':1,'run':'private-run',
                            'unit':'private-unit','state':'running','created':1},key)
        self.assertEqual(event['scope_id'],scope['scope_id'])
        self.assertEqual(_attributes(event)['kind'],'local_scope_launch_audit')
        self.assertNotIn('private',str(event))
        self.assertNotIn('pid',event);self.assertNotIn('nonce_hash',event)
        recovery=project_event('local_scope_recovery_audit',{'id':3,'schema_version':1,'run':'private-run',
                               'unit':'private-unit','status':'reconciled','created':1},key)
        self.assertEqual(recovery['scope_id'],scope['scope_id'])
        self.assertEqual(_attributes(recovery)['status'],'reconciled')
        self.assertNotIn('private',str(recovery))
        stop=project_event('local_scope_stop_audit',{'id':4,'schema_version':1,'run':'private-run','unit':'private-unit',
                           'invocation':'private-invocation','cgroup':'private-cgroup','nonce_hash':'private-nonce',
                           'status':'completed','created':1},key)
        self.assertEqual(stop['scope_id'],scope['scope_id']);self.assertEqual(_attributes(stop)['status'],'completed')
        self.assertNotIn('private',str(stop));self.assertNotIn('nonce_hash',stop)
        reconciled=project_event('local_scope_recovery_audit',{'id':5,'schema_version':1,'run':'private-run',
                                 'unit':'private-unit','status':'reconciled_stop','created':1},key)
        self.assertEqual(_attributes(reconciled)['status'],'reconciled_stop')

    def test_checkpoint_delivery_replays_without_exposing_worker_identities(self):
        import sqlite3
        from types import SimpleNamespace
        from runner.checkpoint import checkpoint
        from runner.local_scope import LocalScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.service import Service
        from tests.test_local_scope import Backend
        with tempfile.TemporaryDirectory() as root:
            service=Service(root);service.lifecycle=ManualLifecycle(root+'/state.db','private-target','private-old')
            cursor=sqlite3.connect(root+'/cursor.db',isolation_level=None)
            try:
                scopes=LocalScopes(root+'/state.db',Backend());scopes.close()
                service.graph.db.execute('CREATE TABLE local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                service.model_supervisor=SimpleNamespace(db=root+'/state.db')
                service.create('private-run','marketing','task');service.enqueue('private-run')
                queue=service.lifecycle.claim()['token'];claim=service.claim('private-run')
                checkpoint(service,'private-run',queue,claim)
                service.lifecycle.register_checkpoint_replacement('private-run','private-old','private-new')
                failed=[];delivered=[]
                def reject(events):failed.extend(events);return False
                def accept(events):delivered.extend(events);return True
                args=(service.graph.db,cursor,'destination','checkpoint_audit',10,b'x'*32)
                self.assertEqual(deliver_page(*args,reject),{'status':'unavailable','cursor':0})
                self.assertEqual(deliver_page(*args,accept),{'status':'exported','cursor':2})
                self.assertEqual(failed,delivered)
                self.assertEqual([event['status'] for event in delivered],['checkpointed','replacement_registered'])
                self.assertNotIn('private',str(delivered))
                self.assertEqual(deliver_page(*args,accept)['status'],'empty')
            finally:cursor.close();service.lifecycle.close();service.close()

    def test_revoked_approval_projects_as_revoked_without_private_fields(self):
        from runner.otel_audit import _attributes
        event=project_event('action_audit',{'id':1,'schema_version':1,'run':'private-run','action':'private-action','version':'private-version','actor':'private-owner','status':'revoked','created':1},b'x'*32)
        self.assertEqual(event['status'],'revoked')
        self.assertEqual(_attributes(event)['status'],'revoked')
        self.assertNotIn('private',str(event))
    def test_workspace_scope_binding_projection_correlates_without_raw_ids(self):
        from runner.otel_audit import _attributes
        row={'id':1,'schema_version':1,'run':'private-run','assignment':'private-assignment','unit':'private-unit','status':'bound','created':1}
        event=project_event('workspace_scope_audit',row,b'x'*32)
        scope=project_event('local_scope_audit',{'id':2,'schema_version':1,'run':'private-run','unit':'private-unit','state':'starting','created':1},b'x'*32)
        self.assertEqual(event['scope_id'],scope['scope_id'])
        self.assertIsNotNone(event['execution_id']);self.assertEqual(event['status'],'bound')
        self.assertNotIn('private',str(event));self.assertEqual(_attributes(event)['kind'],'workspace_scope_audit')

    @unittest.skipUnless(OTLPSpanExporter is not None, 'optional OpenTelemetry OTLP exporter unavailable')
    def test_unavailable_otlp_receiver_replays_after_cursor_restart(self):
        import http.server
        import socket
        import sqlite3
        import threading
        import requests
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        from runner.opik_export import create_opik_trace_exporter, PATH
        class Receiver(http.server.BaseHTTPRequestHandler):
            payloads=[]
            def log_message(self,*args): pass
            def do_POST(self):
                Receiver.payloads.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200); self.send_header('Content-Length','0'); self.end_headers()
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/state.db'); graph.register(engineering_definition()); graph.create('private-run','engineering'); claim=graph.claim('private-run',60)
            graph.finish('private-run','intake',graph.prepare('private-run','intake',claim),'passed','private-content',claim)
            cursor=sqlite3.connect(root+'/cursor.db',isolation_level=None); session=requests.Session(); session.trust_env=False
            reservation=socket.socket(); reservation.bind(('127.0.0.1',0)); port=reservation.getsockname()[1]
            server=None; thread=None
            try:
                adapter=create_opik_trace_exporter(f'http://127.0.0.1:{port}{PATH}',session,timeout=.5)
                self.assertEqual(deliver_page(graph.db,cursor,'local-fixture','graph_audit',10,b'x'*32,adapter),{'status':'unavailable','cursor':0})
                cursor.close(); cursor=sqlite3.connect(root+'/cursor.db',isolation_level=None)
                reservation.close()
                server=http.server.ThreadingHTTPServer(('127.0.0.1',port),Receiver); thread=threading.Thread(target=server.serve_forever); thread.start()
                self.assertEqual(deliver_page(graph.db,cursor,'local-fixture','graph_audit',10,b'x'*32,adapter),{'status':'exported','cursor':1})
                self.assertEqual(deliver_page(graph.db,cursor,'local-fixture','graph_audit',10,b'x'*32,adapter)['status'],'empty')
                self.assertEqual(len(Receiver.payloads),1)
                self.assertNotIn(b'private',Receiver.payloads[0])
                self.assertEqual(len(ExportTraceServiceRequest.FromString(Receiver.payloads[0]).resource_spans[0].scope_spans[0].spans),2)
            finally:
                reservation.close()
                if server is not None: server.shutdown(); thread.join(); server.server_close()
                session.close(); cursor.close(); graph.close()

    def test_platform_review_role_is_preserved_in_sanitized_projection(self):
        from runner.otel_audit import _attributes
        event=project_event('execution_launch_audit',{'id':1,'schema_version':1,'run':'private',
                            'role':'platform_review','status':'completed','created':1},b'x'*32)
        self.assertEqual(event['role'],'platform_review')
        self.assertEqual(_attributes(event)['role'],'platform_review')
        self.assertNotIn('private',str(event))
    @unittest.skipUnless(OTLPSpanExporter is not None, 'optional OpenTelemetry OTLP exporter unavailable')
    def test_opik_adapter_retries_partial_ack_over_local_http(self):
        import http.server
        import sqlite3
        import threading
        import requests
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest, ExportTraceServiceResponse
        from runner.opik_export import create_opik_trace_exporter, PATH
        class Receiver(http.server.BaseHTTPRequestHandler):
            payloads=[]; partial=True
            def log_message(self,*args): pass
            def do_POST(self):
                payload=self.rfile.read(int(self.headers['Content-Length']))
                Receiver.payloads.append((self.path,self.headers.get('Content-Type'),payload))
                response=ExportTraceServiceResponse(partial_success={'rejected_spans':1} if Receiver.partial else {}).SerializeToString()
                self.send_response(200); self.send_header('Content-Length',str(len(response))); self.end_headers(); self.wfile.write(response)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Receiver); thread=threading.Thread(target=server.serve_forever); thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                graph=GraphStore(root+'/state.db'); graph.register(engineering_definition()); graph.create('private-run','engineering'); claim=graph.claim('private-run',60)
                graph.finish('private-run','intake',graph.prepare('private-run','intake',claim),'passed','private-content',claim)
                cursor=sqlite3.connect(root+'/cursor.db',isolation_level=None); session=requests.Session(); session.trust_env=False
                try:
                    adapter=create_opik_trace_exporter(f'http://127.0.0.1:{server.server_port}{PATH}',session,timeout=2)
                    self.assertEqual(deliver_page(graph.db,cursor,'local-fixture','graph_audit',10,b'x'*32,adapter),{'status':'unavailable','cursor':0})
                    cursor.close(); cursor=sqlite3.connect(root+'/cursor.db',isolation_level=None)
                    Receiver.partial=False
                    self.assertEqual(deliver_page(graph.db,cursor,'local-fixture','graph_audit',10,b'x'*32,adapter),{'status':'exported','cursor':1})
                    self.assertEqual(len(Receiver.payloads),2)
                    identities=[]
                    for path,content_type,payload in Receiver.payloads:
                        self.assertEqual((path,content_type),(PATH,'application/x-protobuf'))
                        self.assertNotIn(b'private',payload)
                        spans=ExportTraceServiceRequest.FromString(payload).resource_spans[0].scope_spans[0].spans
                        self.assertEqual(len(spans),2)
                        identities.append([(span.trace_id,span.span_id) for span in spans])
                    self.assertEqual(identities[0],identities[1])
                    self.assertEqual(deliver_page(graph.db,cursor,'local-fixture','graph_audit',10,b'x'*32,adapter)['status'],'empty')
                finally: session.close(); cursor.close(); graph.close()
        finally: server.shutdown(); thread.join(); server.server_close()

    @unittest.skipUnless(OTLPSpanExporter is not None, 'optional OpenTelemetry OTLP exporter unavailable')
    def test_strict_otlp_trace_ack_rejects_partial_and_malformed_responses(self):
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
        class Response:
            ok=True; status_code=200; reason='OK'; content=b''
        class Session:
            headers={}
            def post(self,**kwargs): return self.response
        session=Session(); session.response=Response()
        exporter=OTLPSpanExporter(endpoint='http://127.0.0.1:4318/v1/traces',session=StrictOTLPTraceSession(session),timeout=1)
        adapter=OpenTelemetryAuditTraceExporter(exporter)
        event=project_event('job_audit',{'id':1,'schema_version':1,'run':'private','status':'running','created':1},b'x'*32)
        self.assertTrue(adapter([event]))
        session.response.content=ExportTraceServiceResponse(partial_success={'rejected_spans':1}).SerializeToString()
        self.assertFalse(adapter([event]))
        session.response.content=b'\xff'
        self.assertFalse(adapter([event]))
        session.response.content=b''; session.response.status_code=302
        self.assertFalse(adapter([event]))

    @unittest.skipUnless(OTLPSpanExporter is not None, 'optional OpenTelemetry OTLP exporter unavailable')
    def test_explicit_opik_export_rejects_unsafe_endpoint_and_ambient_configuration(self):
        import os
        from unittest.mock import patch
        from runner.opik_export import create_opik_trace_exporter
        class Response:
            ok=True; status_code=200; content=b''
        class Session:
            trust_env=False
            def __init__(self): self.headers={}; self.calls=[]
            def post(self,**kwargs): self.calls.append(kwargs); return Response()
        session=Session(); event=project_event('job_audit',{'id':1,'schema_version':1,'run':'private','status':'running','created':1},b'x'*32)
        with patch.dict(os.environ,{'OTEL_EXPORTER_OTLP_TRACES_HEADERS':'Authorization=private-ambient','OTEL_EXPORTER_OTLP_TRACES_CLIENT_CERTIFICATE':'/private/ambient','OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE':'/private/ambient'}):
            adapter=create_opik_trace_exporter('http://127.0.0.1:5173/api/v1/private/otel/v1/traces',session,timeout=1)
            self.assertTrue(adapter([event]))
        self.assertFalse(session.calls[0]['allow_redirects']); self.assertIs(session.calls[0]['verify'],True)
        self.assertIsNone(session.calls[0]['cert']); self.assertNotIn('private-ambient',str(session.headers))
        for endpoint in ('http://example.com/api/v1/private/otel/v1/traces','https://user:secret@example.com/api/v1/private/otel/v1/traces','https://example.com/api/v1/private/otel/v1/traces?token=x','https://example.com/wrong'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError): create_opik_trace_exporter(endpoint,Session())
        untrusted=Session(); untrusted.trust_env=True
        with self.assertRaises(ValueError): create_opik_trace_exporter('https://example.com/api/v1/private/otel/v1/traces',untrusted)

    @unittest.skipUnless(OTLPSpanExporter is not None, 'optional OpenTelemetry OTLP exporter unavailable')
    def test_both_workflow_histories_replay_through_strict_opik_adapter(self):
        import sqlite3
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        from runner.graph import marketing_definition
        from runner.opik_export import create_opik_trace_exporter
        class Response:
            ok=True; status_code=200; content=b''
        class Session:
            trust_env=False
            def __init__(self): self.headers={}; self.payloads=[]
            def post(self,**kwargs): self.payloads.append(kwargs['data']); return Response()
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/state.db'); graph.register(engineering_definition()); graph.register(marketing_definition())
            cursor=sqlite3.connect(root+'/cursor.db',isolation_level=None); session=Session()
            try:
                for run,workflow in [('private-engineering','engineering'),('private-marketing','marketing')]:
                    graph.create(run,workflow); token=graph.claim(run,60)
                    graph.finish(run,'intake',graph.prepare(run,'intake',token),'passed','private-content',token)
                adapter=create_opik_trace_exporter('http://127.0.0.1:5173/api/v1/private/otel/v1/traces',session,1)
                result=deliver_page(graph.db,cursor,'fixture-opik','graph_audit',10,b'k'*32,adapter)
                self.assertEqual(result,{'status':'exported','cursor':2})
                spans=ExportTraceServiceRequest.FromString(session.payloads[0]).resource_spans[0].scope_spans[0].spans
                self.assertEqual(len(spans),4); self.assertEqual(sum(not item.parent_span_id for item in spans),2)
                self.assertNotIn(b'private',session.payloads[0])
                cursor.close(); cursor=sqlite3.connect(root+'/cursor.db',isolation_level=None)
                self.assertEqual(deliver_page(graph.db,cursor,'fixture-opik','graph_audit',10,b'k'*32,adapter)['status'],'empty')
                self.assertEqual(len(session.payloads),1)
            finally: cursor.close(); graph.close()
    def test_delivery_restarts_without_acknowledging_failed_export(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/state.db'); graph.register(engineering_definition()); graph.create('private','engineering'); token=graph.claim('private',60)
            graph.finish('private','intake',graph.prepare('private','intake',token),'passed','secret',token)
            cursors=sqlite3.connect(root+'/cursor.db',isolation_level=None)
            try:
                self.assertEqual(deliver_page(graph.db,cursors,'private-destination','graph_audit',10,b'x'*32),{'status':'disabled'})
                failed=deliver_page(graph.db,cursors,'private-destination','graph_audit',10,b'x'*32,lambda _:False)
                self.assertEqual(failed,{'status':'unavailable','cursor':0})
                cursors.close(); cursors=sqlite3.connect(root+'/cursor.db',isolation_level=None)
                seen=[]; result=deliver_page(graph.db,cursors,'private-destination','graph_audit',10,b'x'*32,lambda events:seen.extend(events) or True)
                self.assertEqual(result,{'status':'exported','cursor':1}); self.assertEqual(len(seen),1)
                self.assertEqual(deliver_page(graph.db,cursors,'private-destination','graph_audit',10,b'x'*32,lambda _:True)['status'],'empty')
                self.assertEqual(deliver_page(graph.db,cursors,'private-destination','graph_audit',10,b'y'*32,lambda _:True)['status'],'exported')
                self.assertNotIn('private',str(cursors.execute('SELECT * FROM export_cursors').fetchall()))
                with self.assertRaises(ValueError): deliver_page(graph.db,graph.db,'x','graph_audit',10,b'x'*32,lambda _:True)
            finally: cursors.close(); graph.close()

    def test_concurrent_delivery_never_moves_cursor_backwards(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/state.db'); graph.register(engineering_definition()); graph.create('run','engineering'); token=graph.claim('run',60)
            for stage in ('intake','plan'): graph.finish('run',stage,graph.prepare('run',stage,token),'passed','result',token)
            first=sqlite3.connect(root+'/cursor.db',isolation_level=None); second=sqlite3.connect(root+'/cursor.db',isolation_level=None)
            try:
                def racing(_events):
                    other=deliver_page(graph.db,second,'target','graph_audit',2,b'x'*32,lambda _:True)
                    self.assertEqual(other,{'status':'exported','cursor':2}); return True
                result=deliver_page(graph.db,first,'target','graph_audit',1,b'x'*32,racing)
                self.assertEqual(result,{'status':'superseded','cursor':2})
                self.assertEqual(deliver_page(graph.db,first,'target','graph_audit',1,b'x'*32,lambda _:True)['status'],'empty')
            finally: first.close(); second.close(); graph.close()
    @unittest.skipUnless(LogRecordExportResult is not None, 'optional OpenTelemetry SDK unavailable')
    def test_otel_exporter_only_receives_projection_and_checks_result(self):
        class Exporter:
            def export(self, records):
                self.records=records
                return LogRecordExportResult.SUCCESS
        exporter=Exporter(); adapter=OpenTelemetryAuditExporter(exporter)
        event=project_event('job_audit',{'id':1,'schema_version':1,'run':'private','status':'running','created':1},b'x'*32)
        self.assertTrue(adapter([event]))
        self.assertEqual(exporter.records[0].log_record.body,'workflow.audit')
        self.assertNotIn('private',str(exporter.records[0].log_record.attributes))
        self.assertEqual(dict(exporter.records[0].resource.attributes),{'service.name':'agent-workflows'})
        with self.assertRaises(ValueError): adapter([{**event,'secret':'unsafe'}])
        exporter.export=lambda records: LogRecordExportResult.FAILURE
        self.assertFalse(adapter([event]))

    @unittest.skipUnless(OTLPLogExporter is not None, 'optional OpenTelemetry OTLP exporter unavailable')
    def test_otlp_http_sdk_serializes_only_projected_data(self):
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
        class Response:
            ok=True
            status_code=200
        class Session:
            def __init__(self): self.headers={}; self.payload=None
            def post(self, **kwargs): self.payload=kwargs['data']; return Response()
        session=Session()
        exporter=OTLPLogExporter(endpoint='http://127.0.0.1:4318/v1/logs',session=session,timeout=1)
        event=project_event('job_audit',{'id':1,'schema_version':1,'run':'private','status':'running','created':1,'token':'secret'},b'x'*32)
        self.assertTrue(OpenTelemetryAuditExporter(exporter)([event]))
        request=ExportLogsServiceRequest.FromString(session.payload)
        record=request.resource_logs[0].scope_logs[0].log_records[0]
        self.assertEqual(record.body.string_value,'workflow.audit')
        self.assertNotIn(b'private',session.payload)
        self.assertNotIn(b'secret',session.payload)
    @unittest.skipUnless(OTLPSpanExporter is not None, 'optional OpenTelemetry OTLP exporter unavailable')
    def test_trace_snapshot_has_root_and_replay_stable_child_without_private_data(self):
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        class Response:
            ok=True
            status_code=200
        class Session:
            def __init__(self): self.headers={}; self.payload=None
            def post(self, **kwargs): self.payload=kwargs['data']; return Response()
        session=Session(); exporter=OTLPSpanExporter(endpoint='http://127.0.0.1:4318/v1/traces',session=session,timeout=1)
        event=project_event('graph_audit',{'id':1,'schema_version':1,'run':'private','stage':'intake','status':'passed','created':2,'token':'secret'},b'x'*32)
        adapter=OpenTelemetryAuditTraceExporter(exporter)
        self.assertTrue(adapter([event])); first=session.payload
        request=ExportTraceServiceRequest.FromString(first)
        spans=request.resource_spans[0].scope_spans[0].spans
        self.assertEqual(len(spans),2)
        self.assertEqual(spans[0].parent_span_id,b'')
        self.assertEqual(spans[1].parent_span_id,spans[0].span_id)
        self.assertEqual(spans[0].trace_id,spans[1].trace_id)
        self.assertNotIn(b'private',first); self.assertNotIn(b'secret',first)
        self.assertTrue(adapter([event])); self.assertEqual(session.payload,first)
        with self.assertRaises(ValueError): adapter([event,event])
        other=project_event('graph_audit',{'id':2,'schema_version':1,'run':'other','stage':'intake','status':'blocked','created':3},b'x'*32)
        self.assertTrue(adapter([event,other]))
        spans=ExportTraceServiceRequest.FromString(session.payload).resource_spans[0].scope_spans[0].spans
        self.assertEqual(len(spans),4)
        self.assertEqual(sum(not item.parent_span_id for item in spans),2)
    def test_projection_drops_private_fields_and_correlates_across_workflows(self):
        key=b'x'*32
        private={'id':1,'schema_version':1,'run':'private run','stage':'intake','status':'passed','created':1.5,'version':'secret','actor':'secret','token':'secret'}
        event=project_event('graph_audit',private,key)
        self.assertEqual(event['stage'],'intake')
        self.assertEqual(event['status'],'passed')
        self.assertNotIn('secret',str(event))
        self.assertNotIn('private run',str(event))
        other=project_event('job_audit',{'id':1,'schema_version':1,'run':'private run','status':'running','created':2},key)
        self.assertEqual(event['run_id'],other['run_id'])
        self.assertNotEqual(event['event_id'],other['event_id'])
        self.assertNotEqual(event['run_id'],project_event('graph_audit',private,b'y'*32)['run_id'])

    def test_task_and_attempt_correlation_keeps_identifiers_private(self):
        key=b'x'*32
        row={'id':1,'schema_version':1,'run':'private-run','task_id':'private-task','status':'admitted','created':1}
        admission=project_event('task_admission_audit',row,key)
        claim=project_event('claim_audit',{**row,'id':2,'status':'claimed'},key)
        self.assertEqual(admission['task_id'],claim['task_id'])
        self.assertIsNotNone(claim['task_id'])
        attempt={'id':1,'schema_version':1,'run':'private-run','stage':'intake','version':'private-version','count':1,'status':'uncertain','created':1}
        first=project_event('attempt_audit',attempt,key)
        completed=project_event('attempt_audit',{**attempt,'id':2,'status':'passed'},key)
        self.assertEqual(first['attempt_id'],completed['attempt_id'])
        self.assertNotEqual(first['attempt_id'],project_event('attempt_audit',{**attempt,'count':2},key)['attempt_id'])
        self.assertNotIn('private',str(admission)+str(first))
        from runner.otel_audit import _attributes
        self.assertEqual(_attributes(first)['attempt_id'],first['attempt_id'])
        first={k:v for k,v in first.items() if k!='scope_id'}
        legacy={k:v for k,v in first.items() if k not in ('task_id','attempt_id','reason','execution_id','role','tool_kind','input_present','output_present')}; legacy['schema_version']=1
        self.assertEqual(_attributes(legacy)['schema_version'],1)
        v2={k:v for k,v in first.items() if k not in ('reason','execution_id','role','tool_kind','input_present','output_present')}; v2['schema_version']=2
        self.assertEqual(_attributes(v2)['schema_version'],2)
        v3={k:v for k,v in first.items() if k not in ('execution_id','role','tool_kind','input_present','output_present')}; v3['schema_version']=3
        self.assertEqual(_attributes(v3)['schema_version'],3)
        v4={k:v for k,v in first.items() if k not in ('role','tool_kind','input_present','output_present')}; v4['schema_version']=4
        self.assertEqual(_attributes(v4)['schema_version'],4)
        v5={k:v for k,v in first.items() if k not in ('tool_kind','input_present','output_present')}; v5['schema_version']=5
        self.assertEqual(_attributes(v5)['schema_version'],5)
        with self.assertRaises(ValueError): _attributes({**first,'attempt_id':'private-version'})

    def test_durable_attempt_transitions_export_same_correlation(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/state.db'); graph.register(engineering_definition())
            try:
                graph.create('private-run','engineering','private-task'); token=graph.claim('private-run',60)
                graph.begin_attempt('private-run','intake','private-input',token,2)
                graph.resolve_attempt('private-run','intake','private-input',token,'failed')
                events,_=read_events(graph.db,'attempt_audit',0,10,b'x'*32)
                self.assertEqual([e['status'] for e in events],['uncertain','failed'])
                self.assertIsNotNone(events[0]['attempt_id'])
                self.assertEqual(events[0]['attempt_id'],events[1]['attempt_id'])
                claims,_=read_events(graph.db,'claim_audit',0,10,b'x'*32)
                self.assertIsNotNone(claims[0]['task_id'])
                self.assertNotIn('private',str(events)+str(claims))
            finally: graph.close()

    def test_cache_reuse_projection_does_not_export_content_hashes(self):
        event=project_event('cache_audit',{'id':1,'schema_version':1,'run':'private-run','stage':'brief','status':'reused','input_hash':'secret-input','output_hash':'secret-output','created':1},b'x'*32)
        self.assertEqual(event['status'],'reused')
        self.assertEqual(event['stage'],'brief')
        self.assertNotIn('secret',str(event))
        invalidation=project_event('invalidation_audit',{'id':1,'schema_version':1,'run':'private-run','stage':'brief','status':'invalidated','reason':'gate_changed','created':1},b'x'*32)
        self.assertEqual(invalidation['reason'],'gate_changed')
        invalidation=project_event('invalidation_audit',{'id':2,'schema_version':1,'run':'private-run','stage':'linkedin_review','status':'invalidated','reason':'review_context_changed','created':1},b'x'*32)
        self.assertEqual(invalidation['reason'],'review_context_changed')
        self.assertIsNone(project_event('invalidation_audit',{'id':1,'schema_version':1,'run':'private-run','stage':'brief','status':'invalidated','reason':'private reason','created':1},b'x'*32)['reason'])

    def test_launch_projection_correlates_requested_and_uncertain(self):
        row={'id':1,'schema_version':1,'run':'private-run','role':'brief','execution':'private-execution','status':'requested','model':'private-model','created':1}
        start=project_event('execution_launch_audit',row,b'x'*32)
        end=project_event('execution_launch_audit',{**row,'id':2,'status':'uncertain'},b'x'*32)
        self.assertEqual(start['execution_id'],end['execution_id'])
        self.assertIsNotNone(start['execution_id'])
        self.assertNotIn('private',str(start))
        linked=project_event('execution_link_audit',{**row,'id':3,'session':'private-session','turn':'private-turn','status':'completed'},b'x'*32)
        self.assertEqual(start['execution_id'],linked['execution_id'])
        self.assertIsNotNone(linked['session_id']); self.assertIsNotNone(linked['turn_id'])
        self.assertNotIn('private',str(linked))
        source=project_event('execution_launch_audit',{**row,'role':'source_review'},b'x'*32)
        self.assertEqual(source['role'],'source_review'); self.assertIsNone(source['stage'])
        from runner.otel_audit import _attributes
        self.assertEqual(_attributes(source)['role'],'source_review')
        self.assertIsNone(project_event('execution_launch_audit',{**row,'role':'private role'},b'x'*32)['role'])
        with self.assertRaises(ValueError): _attributes({**source,'role':'private role'})

    def test_tool_projection_excludes_private_payload_and_preserves_shape(self):
        row={'id':1,'schema_version':1,'run':'private-run','role':'source_review','session':'private-session','turn':'private-turn','tool_kind':'mcpToolCall','status':'completed','input_present':1,'output_present':1,'evidence_ref':'private-evidence','created':1}
        event=project_event('tool_audit',row,b'x'*32)
        self.assertEqual((event['tool_kind'],event['input_present'],event['output_present']),('mcpToolCall',True,True))
        self.assertNotIn('private',str(event))
        from runner.otel_audit import _attributes
        self.assertEqual(_attributes(event)['tool_kind'],'mcpToolCall')
        altered=project_event('tool_audit',{**row,'tool_kind':['private'],'input_present':'private'},b'x'*32)
        self.assertIsNone(altered['tool_kind']); self.assertIsNone(altered['input_present'])
        with self.assertRaises(ValueError): _attributes({**event,'input_present':'private'})
        command=project_event('tool_audit',{**row,'tool_kind':'commandExecution','status':'observed'},b'x'*32)
        self.assertEqual((_attributes(command)['tool_kind'],command['status']),('commandExecution','observed'))

    def test_execution_session_is_correlated_without_exposing_private_data(self):
        key=b'x'*32
        row={'id':1,'schema_version':1,'run':'private-run','role':'canonical_draft','session':'private-session','status':'bound','skills':'private-skill','created':1}
        first=project_event('execution_audit',row,key)
        second=project_event('execution_audit',{**row,'id':2,'status':'completed','output_hash':'private-output'},key)
        self.assertEqual(first['session_id'],second['session_id'])
        self.assertNotIn('private',str(first)+str(second))
        self.assertNotEqual(first['session_id'],project_event('execution_audit',row,b'y'*32)['session_id'])
        turn=project_event('execution_turn_audit',{**row,'turn':'private-turn'},key)
        self.assertEqual(first['session_id'],turn['session_id']); self.assertIsNotNone(turn['turn_id'])
        self.assertNotIn('private',str(turn))
        command=project_event('command_audit',{'id':1,'schema_version':1,'run':'private-run','assignment':'private-command','status':'uncertain','created':1,'record_ref':'private-output'},key)
        self.assertIsNotNone(command['command_id']); self.assertNotIn('private',str(command))

    def test_untrusted_values_cannot_leak_and_bad_contract_is_rejected(self):
        row={'id':1,'schema_version':1,'run':'r','stage':'private stage','status':'private status','confirmation':['secret'],'created':1}
        event=project_event('manual_lifecycle_audit',{**row,'state':'stopped'},b'x'*32)
        self.assertIsNone(event['confirmation'])
        self.assertNotIn('secret',str(event))
        with self.assertRaises(ValueError): project_event('unknown',row,b'x'*32)
        with self.assertRaises(ValueError): project_event('graph_audit',row,b'short')
        with self.assertRaises(ValueError): project_event('graph_audit',{**row,'created':float('nan')},b'x'*32)

    def test_replay_reads_durable_history_in_pages(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/state.db')
            try:
                graph.register(engineering_definition()); graph.create('private','engineering'); token=graph.claim('private',60)
                graph.finish('private','intake',graph.prepare('private','intake',token),'passed','secret',token)
                graph.finish('private','plan',graph.prepare('private','plan',token),'blocked','secret',token)
                first,cursor=read_events(graph.db,'graph_audit',0,1,b'x'*32)
                second,last=read_events(graph.db,'graph_audit',cursor,1,b'x'*32)
                self.assertEqual([first[0]['stage'],second[0]['stage']],['intake','plan'])
                self.assertEqual(read_events(graph.db,'graph_audit',last,1,b'x'*32),([],last))
                self.assertNotIn('secret',str(first+second))
                with self.assertRaises(ValueError): read_events(graph.db,'graph_audit',0,501,b'x'*32)
                self.assertEqual(export_page(graph.db,'graph_audit',0,10,b'x'*32),{'status':'disabled','cursor':0})
                def fail(_events): raise RuntimeError('export failed')
                self.assertEqual(export_page(graph.db,'graph_audit',0,10,b'x'*32,fail),{'status':'unavailable','cursor':0})
                received=[]
                def accept(events): received.extend(events); return True
                self.assertEqual(export_page(graph.db,'graph_audit',0,10,b'x'*32,accept),{'status':'exported','cursor':last})
                self.assertEqual(len(received),2)
                self.assertEqual(graph.get('private','plan')['status'],'blocked')
            finally: graph.close()
