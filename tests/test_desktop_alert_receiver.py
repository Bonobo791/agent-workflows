import http.client
import json
import threading
import unittest

from runner.desktop_alert_receiver import build_server


class DesktopAlertReceiverTests(unittest.TestCase):
    def setUp(self):
        self.received=[]
        self.server=build_server('x'*40,lambda title,body:self.received.append((title,body)),port=0)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self,payload,token='x'*40):
        data=json.dumps(payload).encode()
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            connection.request('POST','/alerts',body=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            response=connection.getresponse();response.read();return response.status
        finally:connection.close()

    def test_known_alerts_use_fixed_messages_for_firing_and_resolved(self):
        payload={'alerts':[{'status':'firing','labels':{'alertname':'WorkflowExpiredLease'},'annotations':{'summary':'untrusted private data'}},{'status':'resolved','labels':{'alertname':'WorkflowQuotaPaused'}}]}
        self.assertEqual(self.request(payload),204)
        self.assertEqual(len(self.received),2)
        self.assertNotIn('untrusted',str(self.received))
        self.assertIn('expired',str(self.received[0]).lower())
        self.assertIn('resolved',str(self.received[1]).lower())

    def test_unauthorized_or_unknown_alerts_do_not_notify(self):
        payload={'alerts':[{'status':'firing','labels':{'alertname':'WorkflowExpiredLease'}}]}
        self.assertEqual(self.request(payload,'wrong'),403)
        self.assertEqual(self.request({'alerts':[{'status':'firing','labels':{'alertname':'unknown'}}]}),400)
        self.assertEqual(self.request({'alerts':[{'status':'firing','labels':{'alertname':'WorkflowExpiredLease'}},{'status':'bad','labels':{'alertname':'WorkflowQuotaPaused'}}]}),400)
        self.assertEqual(self.received,[])

    def test_invalid_configuration_and_oversized_body_are_rejected(self):
        with self.assertRaises(ValueError):build_server('short',lambda *_:None,port=0)
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            connection.putrequest('POST','/alerts')
            connection.putheader('Authorization','Bearer '+'x'*40)
            connection.putheader('Content-Type','application/json')
            connection.putheader('Content-Length','65537')
            connection.endheaders()
            response=connection.getresponse();self.assertEqual(response.status,413);response.read()
        finally:connection.close()
        self.assertEqual(self.received,[])

    def test_duplicate_authorization_is_rejected(self):
        data=b'{"alerts":[]}'
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            connection.putrequest('POST','/alerts')
            connection.putheader('Authorization','Bearer '+'x'*40)
            connection.putheader('Authorization','Bearer '+'x'*40)
            connection.putheader('Content-Type','application/json')
            connection.putheader('Content-Length',str(len(data)))
            connection.endheaders(data)
            response=connection.getresponse();self.assertEqual(response.status,403);response.read()
        finally:connection.close()
        self.assertEqual(self.received,[])

    def test_desktop_command_uses_fixed_executable_and_no_shell(self):
        from unittest.mock import patch
        from runner.desktop_alert_receiver import desktop_notify
        with patch.dict('os.environ',{'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus','PRIVATE_SECRET':'do-not-forward'},clear=True),patch('runner.desktop_alert_receiver.subprocess.run') as run:
            desktop_notify('title','body')
        args,kwargs=run.call_args
        self.assertEqual(args[0],['/usr/bin/notify-send','--app-name=Agent Workflows','--','title','body'])
        self.assertNotIn('shell',kwargs)
        self.assertTrue(kwargs['check'])
        self.assertEqual(kwargs['env'],{'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus'})

    def test_notification_failure_requests_retry(self):
        def fail(*args):raise OSError('unavailable')
        self.server.notify=fail
        payload={'alerts':[{'status':'firing','labels':{'alertname':'WorkflowExpiredLease'}}]}
        self.assertEqual(self.request(payload),503)

    def test_partial_delivery_requests_retry_without_silently_dropping_alerts(self):
        calls=[]
        def notify(title,body):
            if len(calls)==1:
                calls.append('failed');raise OSError('temporary failure')
            calls.append((title,body))
        self.server.notify=notify
        payload={'alerts':[{'status':'firing','labels':{'alertname':'WorkflowExpiredLease'}},
                           {'status':'firing','labels':{'alertname':'WorkflowQuotaPaused'}}]}
        self.assertEqual(self.request(payload),503)
        self.assertEqual(self.request(payload),204)
        self.assertEqual(calls[0],calls[2])
        self.assertIn('quota',calls[3][1].lower())

    def test_ambiguous_request_framing_is_rejected_without_notification(self):
        for headers in ((('Content-Length','2'),('Content-Length','2')),
                        (('Content-Length','2'),('Transfer-Encoding','chunked')),
                        (('Content-Length','+2'),)):
            with self.subTest(headers=headers):
                connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
                try:
                    connection.putrequest('POST','/alerts')
                    connection.putheader('Authorization','Bearer '+'x'*40)
                    connection.putheader('Content-Type','application/json')
                    for name,value in headers:connection.putheader(name,value)
                    connection.endheaders(b'{}')
                    response=connection.getresponse();self.assertEqual(response.status,400);response.read()
                finally:connection.close()
        self.assertEqual(self.received,[])

    def test_truncated_batch_is_rejected(self):
        payload={'truncatedAlerts':1,'alerts':[{'status':'firing','labels':{'alertname':'WorkflowExpiredLease'}}]}
        self.assertEqual(self.request(payload),400)
        self.assertEqual(self.received,[])
