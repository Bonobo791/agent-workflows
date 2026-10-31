"""Explicit, disabled-by-default Opik OTLP trace exporter construction."""
import ipaddress
import math
from urllib.parse import urlsplit
from runner.otel_audit import OpenTelemetryAuditTraceExporter, StrictOTLPTraceSession

PATH='/api/v1/private/otel/v1/traces'


def create_opik_trace_exporter(endpoint,session,timeout=5):
    """Build an exporter only for an exact private endpoint and bounded session."""
    if not isinstance(endpoint,str) or not endpoint or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=60: raise ValueError('invalid Opik configuration')
    try:
        url=urlsplit(endpoint); host=url.hostname; port=url.port
        if not host or url.username is not None or url.password is not None or url.query or url.fragment or url.path!=PATH or url.scheme not in ('http','https'): raise ValueError()
        if url.scheme=='http':
            try: local=ipaddress.ip_address(host).is_loopback
            except ValueError: local=host.lower()=='localhost'
            if not local: raise ValueError()
        if port is not None and not 1<=port<=65535: raise ValueError()
    except ValueError: raise ValueError('invalid Opik endpoint') from None
    if getattr(session,'trust_env',None) is not False: raise ValueError('explicit HTTP session required')
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter, Compression
    transport=StrictOTLPTraceSession(session)
    exporter=OTLPSpanExporter(endpoint=endpoint,certificate_file=True,headers={'Content-Type':'application/x-protobuf'},timeout=timeout,compression=Compression.NoCompression,session=transport)
    # The SDK otherwise inherits optional client certificates from the process environment.
    exporter._client_cert=None
    return OpenTelemetryAuditTraceExporter(exporter)
