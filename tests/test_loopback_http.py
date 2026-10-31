"""Network regressions for unauthenticated monitoring connection limits."""
import http.client
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from runner.desktop_alert_receiver import build_server as alert_server
from runner.metrics_http import build_server as metrics_server


class LoopbackHttpTests(unittest.TestCase):
    def authorized(self,server,alerts=False):
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=.5)
        try:
            if alerts:
                body=json.dumps({'alerts':[{'status':'firing','labels':{'alertname':'WorkflowExpiredLease'}}]})
                client.request('POST','/alerts',body,{'Authorization':'Bearer '+'x'*40,'Content-Type':'application/json'})
            else:client.request('GET','/metrics',headers={'Authorization':'Bearer '+'x'*40})
            response=client.getresponse();response.read();return response.status
        finally:client.close()

    def test_slow_unauthorized_headers_do_not_block_authenticated_requests(self):
        for alerts in (False,True):
            with self.subTest(alerts=alerts):
                calls=[]
                server=alert_server('x'*40,lambda *args:calls.append(args)) if alerts else metrics_server('x'*40,lambda:'snapshot\n')
                thread=threading.Thread(target=server.serve_forever);thread.start()
                slow=socket.create_connection(('127.0.0.1',server.server_port),timeout=1)
                try:
                    slow.sendall(b'GET /metrics HTTP/1.1\r\nX-Slow: ')
                    time.sleep(.05)
                    self.assertEqual(self.authorized(server,alerts),204 if alerts else 200)
                finally:
                    slow.close();server.shutdown();thread.join(timeout=2);server.server_close()

    def test_trickled_headers_expire_at_absolute_deadline(self):
        with patch('runner.loopback_http.REQUEST_TIMEOUT',.3):
            server=metrics_server('x'*40,lambda:'snapshot\n')
            thread=threading.Thread(target=server.serve_forever);thread.start()
            slow=socket.create_connection(('127.0.0.1',server.server_port),timeout=1)
            try:
                slow.sendall(b'GET /metrics HTTP/1.1\r\nX-Slow: ')
                deadline=time.monotonic()+1
                while time.monotonic()<deadline:
                    try:slow.sendall(b'a')
                    except OSError:break
                    time.sleep(.05)
                self.assertEqual(slow.recv(1),b'')
                self.assertEqual(self.authorized(server),200)
            finally:
                slow.close();server.shutdown();thread.join(timeout=2);server.server_close()

    def test_connection_limit_rejects_excess_work_then_recovers(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def collect():
            calls.append(1);entered.set();release.wait(2);return 'snapshot\n'
        with patch('runner.loopback_http.MAX_CONNECTIONS',1):
            server=metrics_server('x'*40,collect)
            thread=threading.Thread(target=server.serve_forever);thread.start()
            first=socket.create_connection(('127.0.0.1',server.server_port),timeout=2)
            try:
                first.sendall(b'GET /metrics HTTP/1.1\r\nAuthorization: Bearer '+b'x'*40+b'\r\n\r\n')
                self.assertTrue(entered.wait(1))
                with self.assertRaises((OSError,http.client.HTTPException)):self.authorized(server)
                self.assertEqual(len(calls),1)
                release.set();self.assertIn(b'200',first.recv(4096))
                deadline=time.monotonic()+1
                status=None
                while time.monotonic()<deadline:
                    try:status=self.authorized(server);break
                    except (OSError,http.client.HTTPException):time.sleep(.01)
                self.assertEqual(status,200)
            finally:
                release.set();first.close();server.shutdown();thread.join(timeout=2);server.server_close()
