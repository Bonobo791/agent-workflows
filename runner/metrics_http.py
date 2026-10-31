"""Authenticated loopback Prometheus scrape of the private worker snapshot."""
import hmac
import os
import re
import sys
from runner.monitor_http import MonitoringHandler, MonitoringHTTPServer

from runner.metrics_cli import read_metrics
from runner.worker import ConfigurationError, read_secret

MAX_BODY=65536


class Handler(MonitoringHandler):
    def do_GET(self):
        self.finish_reading()
        if self.path!='/metrics': return self.respond(404)
        auth=self.headers.get_all('Authorization',[])
        if len(auth)!=1 or not hmac.compare_digest(auth[0].encode('utf-8'),('Bearer '+self.server.secret).encode('utf-8')): return self.respond(403)
        try:
            snapshot=self.server.collect()
            if not isinstance(snapshot,str): raise ValueError('snapshot unavailable')
            body=snapshot.encode('utf-8')
            if not 1<=len(body)<=MAX_BODY: raise ValueError('snapshot unavailable')
        except Exception:return self.respond(503)
        return self.respond(200,body)

    def respond(self,status,body=b''):
        self.send_response(status)
        if status==200:self.send_header('Content-Type','text/plain; version=0.0.4; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers()
        if body:self.wfile.write(body)


def build_server(secret,collect=read_metrics,port=0):
    if not isinstance(secret,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',secret) or not callable(collect) or type(port) is not int or not 0<=port<=65535: raise ValueError('invalid metrics endpoint configuration')
    server=MonitoringHTTPServer(('127.0.0.1',port),Handler);server.secret=secret;server.collect=collect
    return server


def main():
    path=os.environ.get('WORKFLOW_METRICS_HTTP_SECRET_FILE');port=os.environ.get('WORKFLOW_METRICS_HTTP_PORT')
    if not path or not port or not port.isdecimal() or not 1<=int(port)<=65535: raise ConfigurationError('metrics endpoint configuration required')
    server=build_server(read_secret(path),port=int(port))
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,ConfigurationError):
        print('Metrics endpoint unavailable.',file=sys.stderr);sys.exit(1)
