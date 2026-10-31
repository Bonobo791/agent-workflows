"""Opt-in OpenTelemetry SDK adapter for the sanitized audit projection."""
import math
import re
import hashlib
from runner.audit_export import TABLES, STAGES, STATUSES, CONFIRMATIONS, INVALIDATION_REASONS, ROLES, TOOL_KINDS


class StrictOTLPTraceSession:
    """Reject malformed or partially accepted OTLP trace responses."""
    def __init__(self, session):
        if not callable(getattr(session,'post',None)) or not hasattr(session,'headers'): raise ValueError('HTTP session required')
        self.session=session; self.headers=session.headers
    def post(self, **kwargs):
        kwargs['allow_redirects']=False
        response=self.session.post(**kwargs)
        if not response.ok: return response
        try:
            if type(response.status_code) is not int or not 200<=response.status_code<300: raise ValueError('unexpected HTTP status')
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
            parsed=ExportTraceServiceResponse.FromString(response.content)
            if parsed.partial_success.rejected_spans: raise ValueError('partial trace acceptance')
            return response
        except Exception:
            class Rejected:
                ok=False; status_code=400; reason='OTLP trace acknowledgement unavailable or partial'
            return Rejected()
    def close(self):
        if callable(getattr(self.session,'close',None)): self.session.close()


def _attributes(event):
    base={'schema_version','kind','event_id','run_id','session_id','turn_id','command_id','stage','status','confirmation','timestamp'}
    version=event.get('schema_version') if isinstance(event,dict) else None
    if not isinstance(event,dict) or type(version) is not int or version not in (1,2,3,4,5,6,7): raise ValueError('invalid projected audit event')
    ids=('session_id','turn_id','command_id') + (('task_id','attempt_id') if version>=2 else ()) + (('execution_id',) if version>=4 else ()) + (('scope_id',) if version>=7 else ())
    expected=base | (set(ids)-{'session_id','turn_id','command_id'}) | ({'reason'} if version>=3 else set()) | ({'role'} if version>=5 else set()) | ({'tool_kind','input_present','output_present'} if version>=6 else set())
    if (set(event)!=expected or (version>=3 and event['reason'] is not None and (not isinstance(event['reason'],str) or event['reason'] not in INVALIDATION_REASONS)) or (version>=5 and event['role'] is not None and (not isinstance(event['role'],str) or event['role'] not in ROLES)) or (version>=6 and event['tool_kind'] is not None and (not isinstance(event['tool_kind'],str) or event['tool_kind'] not in TOOL_KINDS)) or (version>=6 and any(event[key] is not None and type(event[key]) is not bool for key in ('input_present','output_present'))) or not isinstance(event['kind'],str) or event['kind'] not in TABLES or not isinstance(event['event_id'],str) or not re.fullmatch(r'[0-9a-f]{64}',event['event_id']) or not isinstance(event['run_id'],str) or not re.fullmatch(r'[0-9a-f]{64}',event['run_id']) or any(event[key] is not None and (not isinstance(event[key],str) or not re.fullmatch(r'[0-9a-f]{64}',event[key])) for key in ids) or (event['stage'] is not None and (not isinstance(event['stage'],str) or event['stage'] not in STAGES)) or not isinstance(event['status'],str) or event['status'] not in STATUSES | {'unavailable'} or (event['confirmation'] is not None and (not isinstance(event['confirmation'],str) or event['confirmation'] not in CONFIRMATIONS)) or not isinstance(event['timestamp'],(int,float)) or isinstance(event['timestamp'],bool) or not math.isfinite(event['timestamp']) or not 0 <= event['timestamp'] < 18446744073):
        raise ValueError('invalid projected audit event')
    return {key:value for key,value in event.items() if key!='timestamp' and value is not None}


class OpenTelemetryAuditExporter:
    def __init__(self, exporter):
        if not callable(getattr(exporter, 'export', None)):
            raise ValueError('OpenTelemetry exporter required')
        self.exporter = exporter

    def __call__(self, events):
        from opentelemetry._logs import LogRecord
        from opentelemetry.sdk._logs import ReadableLogRecord
        from opentelemetry.sdk._logs.export import LogRecordExportResult
        from opentelemetry.sdk.resources import Resource

        resource = Resource({'service.name': 'agent-workflows'})
        records = []
        for event in events:
            attributes = _attributes(event)
            record = LogRecord(timestamp=int(event['timestamp'] * 1_000_000_000), body='workflow.audit', attributes=attributes)
            records.append(ReadableLogRecord(log_record=record, resource=resource))
        return self.exporter.export(records) == LogRecordExportResult.SUCCESS


class OpenTelemetryAuditTraceExporter:
    """Emit bounded audit snapshots as point events, not measured worker spans."""
    def __init__(self, exporter):
        if not callable(getattr(exporter,'export',None)): raise ValueError('OpenTelemetry exporter required')
        self.exporter=exporter

    def __call__(self, events):
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import ReadableSpan
        from opentelemetry.sdk.trace.export import SpanExportResult
        from opentelemetry.trace import SpanContext, TraceFlags

        if not isinstance(events,list) or not 1<=len(events)<=500: raise ValueError('invalid audit snapshot')
        groups={}
        for event in events:
            attributes=_attributes(event)
            groups.setdefault(event['run_id'],[]).append((event,attributes))
        resource=Resource({'service.name':'agent-workflows'})
        spans=[]
        for run_id,items in sorted(groups.items()):
            items.sort(key=lambda item:(item[0]['timestamp'],item[0]['event_id']))
            ids=[event['event_id'] for event,_ in items]
            if len(ids)!=len(set(ids)): raise ValueError('duplicate audit event')
            digest=hashlib.sha256(('audit-snapshot:'+run_id+':'+':'.join(ids)).encode('ascii')).hexdigest()
            trace_id=int(digest[:32],16) or 1; root_id=int(digest[32:48],16) or 1
            root=SpanContext(trace_id,root_id,False,TraceFlags(1))
            times=[int(event['timestamp']*1_000_000_000) for event,_ in items]
            spans.append(ReadableSpan(name='workflow.audit.snapshot',context=root,resource=resource,attributes={'run_id':run_id,'audit.snapshot':True,'audit.event_count':len(items)},start_time=min(times),end_time=max(times)))
            seen={root_id}
            for (event,attributes),timestamp in zip(items,times):
                span_id=int(event['event_id'][:16],16) or 1
                if span_id in seen: raise ValueError('audit span ID collision')
                seen.add(span_id)
                context=SpanContext(trace_id,span_id,False,TraceFlags(1))
                spans.append(ReadableSpan(name='workflow.audit.'+event['kind'],context=context,parent=root,resource=resource,attributes=attributes,start_time=timestamp,end_time=timestamp))
        return self.exporter.export(spans)==SpanExportResult.SUCCESS
