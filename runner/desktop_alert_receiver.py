"""Authenticated loopback bridge from Alertmanager to local desktop alerts."""
import hmac
import json
import os
import re
import subprocess
import sys
from runner.monitor_http import MonitoringHandler, MonitoringHTTPServer

from runner.worker import ConfigurationError, read_secret

MAX_BODY=65536
ALERTS={
    'WorkflowUncertainExecution':'Workflow execution requires inspection',
    'WorkflowExpiredLease':'Workflow lease expired',
    'WorkflowQuotaPaused':'Workflow quota is paused',
    'WorkflowAwaitingApproval':'Workflow approval is waiting',
    'WorkflowMetricsStale':'Workflow metrics are stale',
    'WorkflowMetricsMissing':'Workflow metrics are unavailable',
}


def desktop_notify(title,body):
    names=('DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR','DISPLAY','WAYLAND_DISPLAY','XAUTHORITY','LANG')
    env={name:os.environ[name] for name in names if name in os.environ}
    subprocess.run(['/usr/bin/notify-send','--app-name=Agent Workflows','--',title,body],check=True,timeout=5,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env)


class Handler(MonitoringHandler):
    def do_POST(self):
        try:
            if self.path!='/alerts': return self.respond(404)
            auth=self.headers.get_all('Authorization',[])
            if len(auth)!=1 or not hmac.compare_digest(auth[0].encode('utf-8'),('Bearer '+self.server.secret).encode('utf-8')): return self.respond(403)
            lengths=self.headers.get_all('Content-Length',[])
            if len(lengths)!=1 or not re.fullmatch(r'[0-9]{1,6}',lengths[0]) or self.headers.get('Transfer-Encoding') is not None: return self.respond(400)
            length=int(lengths[0])
            if not 1<=length<=MAX_BODY: return self.respond(413)
            if self.headers.get_content_type()!='application/json': return self.respond(415)
            body=self.rfile.read(length)
            if len(body)!=length: return self.respond(400)
            self.finish_reading()
            payload=json.loads(body)
            if not isinstance(payload,dict) or type(payload.get('truncatedAlerts',0)) is not int or payload.get('truncatedAlerts',0)!=0: return self.respond(400)
            alerts=payload.get('alerts')
            if not isinstance(alerts,list) or not 1<=len(alerts)<=32: return self.respond(400)
            messages=[]
            for alert in alerts:
                if not isinstance(alert,dict) or not isinstance(alert.get('labels'),dict): return self.respond(400)
                name=alert['labels'].get('alertname');status=alert.get('status')
                if not isinstance(name,str) or name not in ALERTS or status not in ('firing','resolved'): return self.respond(400)
                messages.append(('Agent Workflows alert' if status=='firing' else 'Agent Workflows alert resolved',ALERTS[name]))
            try:
                for title,body in messages:self.server.notify(title,body)
            except Exception: return self.respond(503)
            return self.respond(204)
        except (OSError,ValueError,UnicodeError): return self.respond(400)

    def respond(self,status):
        self.send_response(status);self.send_header('Content-Length','0');self.end_headers()


def build_server(secret,notify=desktop_notify,port=0):
    if not isinstance(secret,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',secret) or not callable(notify) or type(port) is not int or not 0<=port<=65535: raise ValueError('invalid alert receiver configuration')
    server=MonitoringHTTPServer(('127.0.0.1',port),Handler);server.secret=secret;server.notify=notify
    return server


def main():
    path=os.environ.get('WORKFLOW_ALERT_SECRET_FILE');port=os.environ.get('WORKFLOW_ALERT_PORT')
    if not path or not port or not port.isdecimal() or not 1<=int(port)<=65535: raise ConfigurationError('alert receiver configuration required')
    server=build_server(read_secret(path),port=int(port))
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,ConfigurationError):
        print('Alert receiver unavailable.',file=sys.stderr);sys.exit(1)
