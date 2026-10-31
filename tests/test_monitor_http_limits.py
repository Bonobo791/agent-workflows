import http.client
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from runner.desktop_alert_receiver import build_server as alert_server
from runner.metrics_http import build_server as metrics_server


class MonitoringHttpLimitsTests(unittest.TestCase):
    def test_incomplete_headers_do_not_block_authenticated_requests(self):
        cases = (
            (lambda: metrics_server('x'*40, lambda: 'metric 1\n'), 'GET', '/metrics', None, 200),
            (lambda: alert_server('x'*40, lambda *_: None), 'POST', '/alerts',
             json.dumps({'alerts': [{'status': 'firing', 'labels': {'alertname': 'WorkflowExpiredLease'}}]}), 204),
        )
        for create, method, path, body, expected in cases:
            with self.subTest(path=path):
                server = create()
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                slow = socket.create_connection(('127.0.0.1', server.server_port), timeout=1)
                try:
                    slow.sendall(b'GET / HTTP/1.1\r\nHost: incomplete')
                    time.sleep(.1)
                    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=1)
                    try:
                        client.request(method, path, body=body, headers={
                            'Authorization': 'Bearer ' + 'x'*40, 'Content-Type': 'application/json'})
                        response = client.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
                    finally:
                        client.close()
                finally:
                    slow.close()
                    server.shutdown()
                    thread.join()
                    server.server_close()

    def test_trickled_headers_and_body_expire_without_notification(self):
        received = []
        payload = json.dumps({'alerts': [{'status': 'firing', 'labels': {'alertname': 'WorkflowExpiredLease'}}]}).encode()
        cases = (
            (lambda: metrics_server('x'*40, lambda: 'metric 1\n'), b'GET /metrics HTTP/1.1\r\nHost: incomplete'),
            (lambda: alert_server('x'*40, lambda *args: received.append(args)),
             b'POST /alerts HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer ' + b'x'*40 +
             b'\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(payload)+100).encode() + b'\r\n\r\n' + payload),
        )
        with patch('runner.monitor_http.READ_TIMEOUT', .4):
            for create, initial in cases:
                server = create()
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                client = socket.create_connection(('127.0.0.1', server.server_port), timeout=1)
                try:
                    client.sendall(initial)
                    closed = False
                    for _ in range(12):
                        time.sleep(.08)
                        try:
                            client.sendall(b' ')
                        except OSError:
                            closed = True
                            break
                    if not closed:
                        client.settimeout(1)
                        closed = client.recv(1024) == b''
                    self.assertTrue(closed, 'trickled request exceeded its total read deadline')
                finally:
                    client.close()
                    server.shutdown()
                    thread.join()
                    server.server_close()
        self.assertEqual(received, [])

    def test_client_limit_recovers_after_read_deadlines(self):
        with patch('runner.monitor_http.MAX_CLIENTS', 2), patch('runner.monitor_http.READ_TIMEOUT', .4):
            server = metrics_server('x'*40, lambda: 'metric 1\n')
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            clients = []
            try:
                for _ in range(2):
                    client = socket.create_connection(('127.0.0.1', server.server_port), timeout=1)
                    clients.append(client)
                    client.sendall(b'GET / HTTP/1.1\r\nHost: incomplete')
                time.sleep(.1)
                rejected = socket.create_connection(('127.0.0.1', server.server_port), timeout=1)
                try:
                    self.assertEqual(rejected.recv(1), b'')
                finally:
                    rejected.close()
                time.sleep(.5)
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=1)
                try:
                    connection.request('GET', '/metrics', headers={'Authorization': 'Bearer ' + 'x'*40})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                finally:
                    connection.close()
            finally:
                for client in clients:
                    client.close()
                server.shutdown()
                thread.join()
                server.server_close()
