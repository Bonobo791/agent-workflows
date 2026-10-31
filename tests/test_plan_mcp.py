import json
import http.server
import io
import os
import shutil
import signal
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
from runner.plan_mcp import PlanMCP, MCPError, HTTPSMCP

class FakeTransport:
    def __init__(self): self.calls=[]
    def post(self,message,headers):
        self.calls.append((message,headers))
        if message['method']=='initialize': return (200,{'Content-Type':'application/json','Mcp-Session-Id':'session'},json.dumps({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}}).encode())
        if message['method']=='notifications/initialized': return (202,{},b'')
        return (200,{'Content-Type':'text/event-stream'},('event: message\ndata: '+json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'structuredContent':{'ok':True,'data':{'identifier':'TEST-1'}}}})+'\n\n').encode())

class MCPTests(unittest.TestCase):
    def test_endpoint_rejects_empty_user_info(self):
        with self.assertRaises(MCPError): HTTPSMCP('https://@example.com/mcp','token')
        from runner import plan_mcp_child
        with patch('sys.stdin',io.StringIO(json.dumps({'endpoint':'https://@example.com/mcp'}))), patch.object(plan_mcp_child.urllib.request,'build_opener') as opener:
            with self.assertRaises(ValueError): plan_mcp_child.main()
            opener.assert_not_called()

    def test_timeout_cleanup_does_not_mask_mcp_error(self):
        from unittest.mock import MagicMock
        process=MagicMock(); process.poll.return_value=None
        process.communicate.side_effect=subprocess.TimeoutExpired('helper',1)
        client=HTTPSMCP('https://example.com/mcp','token')
        with patch('runner.plan_mcp.subprocess.Popen',return_value=process):
            with self.assertRaises(MCPError): client.post({}, {})
        process.kill.assert_called_once()
        self.assertIsNotNone(process.communicate.call_args.kwargs.get('timeout'))

    @unittest.skipUnless(shutil.which('openssl'),'openssl required for local TLS fixture')
    def test_https_request_has_total_deadline_even_with_trickling_response(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                data=self.rfile.read(int(self.headers['Content-Length']))
                if json.loads(data).get('fast'):
                    self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Mcp-Session-Id','session'); self.end_headers()
                    self.wfile.write(self.headers['Authorization'].encode()); return
                self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length','30'); self.end_headers()
                for _ in range(30):
                    try: self.wfile.write(b'x'); self.wfile.flush()
                    except OSError: break
                    time.sleep(.1)
            def log_message(self,*args): pass
        with tempfile.TemporaryDirectory() as root:
            cert=os.path.join(root,'cert.pem'); key=os.path.join(root,'key.pem')
            config=Path(root)/'openssl.cnf'
            config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=extensions\n[dn]\n[extensions]\nsubjectAltName=DNS:localhost\nbasicConstraints=critical,CA:TRUE\nsubjectKeyIdentifier=hash\n')
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',cert,'-days','1','-subj','/CN=localhost','-config',str(config)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert,key); server.socket=context.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                with patch.dict(os.environ,{'SSL_CERT_FILE':cert}), patch.object(HTTPSMCP,'TIMEOUT',1):
                    client=HTTPSMCP(f'https://localhost:{server.server_port}/mcp','test-token')
                    status,headers,body=client.post({'fast':True},{})
                    self.assertEqual((status,headers['Mcp-Session-Id'],body),(200,'session',b'Bearer test-token'))
                    started=time.monotonic()
                    with self.assertRaises(MCPError): client.post({'test':1},{})
                    self.assertLess(time.monotonic()-started,2)
                    child=Path(__file__).resolve().parents[1]/'runner'/'plan_mcp_child.py'
                    data={'endpoint':client.endpoint,'token':'test-token','message':{'test':1},'headers':{},'timeout':.5}
                    result=subprocess.run([sys.executable,'-I',str(child)],input=json.dumps(data).encode(),capture_output=True,timeout=2)
                    self.assertEqual(result.returncode,-signal.SIGALRM)
            finally: server.shutdown(); server.server_close(); thread.join()

    def test_team_scoped_project_key_is_sent_without_rewriting(self):
        transport=FakeTransport(); client=PlanMCP(transport)
        client.get_issue('moderaty.MC',32)
        params=transport.calls[-1][0]['params']
        self.assertEqual(params['name'],'get_issue_by_number')
        self.assertEqual(params['arguments'],{'projectKey':'moderaty.MC','sequenceNumber':32})

    def test_malformed_project_keys_do_not_reach_transport(self):
        for key in ('team..MC','.MC','team.','team.MC.extra','team/MC'):
            transport=FakeTransport(); client=PlanMCP(transport)
            with self.subTest(key=key),self.assertRaises(MCPError): client.get_issue(key,1)
            self.assertEqual(transport.calls,[])

    def test_handshake_and_read_only_tool_call(self):
        transport=FakeTransport(); client=PlanMCP(transport)
        result=client.get_issue('TEST',1)
        self.assertEqual(result['data']['identifier'],'TEST-1')
        self.assertEqual(transport.calls[-1][1]['MCP-Session-Id'],'session')
        self.assertEqual(transport.calls[-1][1]['MCP-Protocol-Version'],'2025-06-18')
    def test_write_tools_cannot_be_called(self):
        client=PlanMCP(FakeTransport())
        with self.assertRaises(MCPError): client.call('update_issue',{})
