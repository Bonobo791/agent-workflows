import http.client
import threading
import unittest

from runner.metrics_http import build_server


class MetricsHttpTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        self.server=build_server('x'*40,lambda:self.calls.append(1) or 'workflow_jobs{status="running"} 1\n',port=0)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.thread.join);self.addCleanup(self.server.shutdown)

    def request(self,path='/metrics',token='x'*40):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            connection.request('GET',path,headers={'Authorization':'Bearer '+token})
            response=connection.getresponse();return response.status,response.getheaders(),response.read()
        finally:connection.close()

    def test_authorized_scrape_returns_current_snapshot(self):
        status,headers,body=self.request()
        self.assertEqual(status,200);self.assertIn(b'workflow_jobs',body)
        self.assertEqual(self.calls,[1]);self.assertNotIn(b'x'*40,body)
        self.assertEqual(dict(headers)['Content-Type'],'text/plain; version=0.0.4; charset=utf-8')

    def test_unauthorized_or_wrong_path_does_not_collect(self):
        self.assertEqual(self.request(token='wrong')[0],403)
        self.assertEqual(self.request(path='/other')[0],404)
        self.assertEqual(self.calls,[])

    def test_duplicate_authorization_does_not_collect(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            connection.putrequest('GET','/metrics')
            for _ in range(2):connection.putheader('Authorization','Bearer '+'x'*40)
            connection.endheaders()
            response=connection.getresponse();self.assertEqual(response.status,403);response.read()
        finally:connection.close()
        self.assertEqual(self.calls,[])

    def test_collection_failure_does_not_publish_stale_or_private_data(self):
        def fail():raise RuntimeError('private fixture')
        self.server.collect=fail
        status,_,body=self.request()
        self.assertEqual(status,503);self.assertNotIn(b'private',body)

    def test_oversized_snapshot_is_not_returned(self):
        self.server.collect=lambda:'x'*65537
        self.assertEqual(self.request()[0],503)

    def test_invalid_secret_is_rejected(self):
        with self.assertRaises(ValueError):build_server('short',lambda:'',port=0)
