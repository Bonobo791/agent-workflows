import ipaddress
import unittest
from unittest.mock import patch
from runner.source_fetch import SourceFetchError, SourceFetcher

class Response:
    def __init__(self,status=200,body=b'primary evidence',content_type='text/plain',encoding='identity'):
        self.status=status;self.body=body;self.content_type=content_type;self.encoding=encoding
    def getheader(self,name,default=None): return self.content_type if name.lower()=='content-type' else self.encoding if name.lower()=='content-encoding' else default
    def read(self,limit): return self.body[:limit]
class Connection:
    def __init__(self,response): self.response=response;self.closed=False;self.requests=[]
    def request(self,*args,**kwargs): self.requests.append((args,kwargs))
    def getresponse(self): return self.response
    def close(self): self.closed=True

class SourceFetchTests(unittest.TestCase):
    def test_successful_connection_restores_remaining_response_timeout(self):
        from runner.source_fetch import _PinnedHTTPS
        class Sock:
            def __init__(self): self.timeouts=[]
            def settimeout(self,value): self.timeouts.append(value)
            def close(self): pass
        sock=Sock(); now=[0]
        def handshake(sock,server_hostname):
            now[0]=4
            return sock
        with patch('runner.source_fetch.time.monotonic',side_effect=lambda:now[0]):
            c=_PinnedHTTPS('docs.example.com',['93.184.216.34','93.184.216.35'],10)
            with patch('runner.source_fetch.socket.create_connection',return_value=sock),patch.object(c._context,'wrap_socket',side_effect=handshake):c.connect()
        self.assertEqual(sock.timeouts,[5,6])
    def test_connect_returning_after_its_slice_falls_back_while_overall_time_remains(self):
        from runner.source_fetch import _PinnedHTTPS
        class Sock:
            def settimeout(self,value): pass
            def close(self): self.closed=True
        now=[0.0]; attempts=[]; socks=[]
        def connect(address,timeout):
            attempts.append(address[0]); sock=Sock(); socks.append(sock)
            if len(attempts)==1: now[0]=6.0
            return sock
        with patch('runner.source_fetch.time.monotonic',side_effect=lambda:now[0]):
            c=_PinnedHTTPS('docs.example.com',['93.184.216.34','93.184.216.35'],10)
            with patch('runner.source_fetch.socket.create_connection',side_effect=connect),patch.object(c._context,'wrap_socket',side_effect=lambda sock,server_hostname:sock):
                c.connect()
        self.assertEqual(attempts,['93.184.216.34','93.184.216.35']); self.assertTrue(socks[0].closed)
        now[0]=0.0; attempts.clear()
        def expired(address,timeout):
            attempts.append(address[0]); now[0]=11.0; return Sock()
        with patch('runner.source_fetch.time.monotonic',side_effect=lambda:now[0]):
            c=_PinnedHTTPS('docs.example.com',['93.184.216.34','93.184.216.35'],10)
            with patch('runner.source_fetch.socket.create_connection',side_effect=expired),self.assertRaises(TimeoutError): c.connect()
        self.assertEqual(attempts,['93.184.216.34'])
    def test_stall_past_a_slice_before_connecting_skips_that_address_instead_of_raising_value_error(self):
        from runner.source_fetch import _PinnedHTTPS
        class Sock:
            def settimeout(self,value): pass
            def close(self): pass
        ticks=iter([0,0,0,6]); last=[6]; attempts=[]
        def clock():
            last[0]=next(ticks,last[0]); return last[0]
        def connect(address,timeout):
            attempts.append((address[0],timeout)); return Sock()
        with patch('runner.source_fetch.time.monotonic',side_effect=clock):
            c=_PinnedHTTPS('docs.example.com',['93.184.216.34','93.184.216.35'],10)
            with patch('runner.source_fetch.socket.create_connection',side_effect=connect),patch.object(c._context,'wrap_socket',side_effect=lambda sock,server_hostname:sock):
                c.connect()
        self.assertEqual(attempts,[('93.184.216.35',4)])
    def test_unresponsive_first_address_leaves_time_for_fallback(self):
        from runner.source_fetch import _PinnedHTTPS
        class Sock:
            def settimeout(self,value): pass
            def close(self): pass
        now=[0.0]; attempts=[]
        def connect(address,timeout):
            attempts.append((address[0],timeout))
            if len(attempts)==1:
                now[0]+=timeout
                raise TimeoutError('first address unresponsive')
            return Sock()
        with patch('runner.source_fetch.time.monotonic',side_effect=lambda:now[0]):
            c=_PinnedHTTPS('docs.example.com',['93.184.216.34','93.184.216.35'],10)
            with patch('runner.source_fetch.socket.create_connection',side_effect=connect),patch.object(c._context,'wrap_socket',side_effect=lambda sock,server_hostname:sock):
                c.connect()
        self.assertEqual([a[0] for a in attempts],['93.184.216.34','93.184.216.35'])
        self.assertLess(attempts[0][1],10)
    def test_already_cancelled_fetch_does_not_launch(self):
        fetcher=SourceFetcher(['https://docs.example.com/'])
        with patch('runner.source_fetch.subprocess.Popen') as launch:
            with self.assertRaises(SourceFetchError): fetcher.fetch('https://docs.example.com/',cancelled=lambda:True)
            launch.assert_not_called()
    def test_dns_cannot_exceed_total_deadline(self):
        import subprocess,sys,tempfile,time
        from pathlib import Path
        fetcher=SourceFetcher(['https://docs.example.com/'],timeout=1)
        def slow_dns(*args,**kwargs): time.sleep(2); return []
        code="""
import pathlib,socket,sys,time
from runner.source_fetch import SourceFetcher
def slow(*args,**kwargs):
 pathlib.Path(sys.argv[1]).write_text('resolving'); time.sleep(2); return []
socket.getaddrinfo=slow
SourceFetcher(['https://docs.example.com/'],timeout=1)._fetch_direct('https://docs.example.com/')
"""
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'dns'; popen=subprocess.Popen; children=[]
            def launch(*args,**kwargs):
                child=popen([sys.executable,'-c',code,str(marker)],**kwargs); children.append(child); return child
            started=time.monotonic()
            with patch('runner.source_fetch.socket.getaddrinfo',side_effect=slow_dns), patch('runner.source_fetch.subprocess.Popen',side_effect=launch):
                with self.assertRaises(SourceFetchError): fetcher.fetch('https://docs.example.com/')
            self.assertLess(time.monotonic()-started,1.6)
            self.assertTrue(marker.exists())
            self.assertEqual(len(children),1); self.assertIsNotNone(children[0].poll())

    def test_helper_enforces_deadline_without_parent_supervision(self):
        import json,signal,subprocess,sys,time
        from pathlib import Path
        path=str(Path(__file__).parents[1]/'runner'/'source_fetch.py')
        code="""
import runpy,socket,sys,time
def slow(*args,**kwargs): time.sleep(2); return []
socket.getaddrinfo=slow
sys.argv=[sys.argv[1],'--fetch']
runpy.run_path(sys.argv[0],run_name='__main__')
"""
        started=time.monotonic()
        result=subprocess.run([sys.executable,'-I','-c',code,path],input=json.dumps({'url':'https://docs.example.com/','max_bytes':1024,'timeout':1}),capture_output=True,text=True,timeout=4)
        self.assertEqual(result.returncode,-signal.SIGALRM)
        self.assertLess(time.monotonic()-started,1.6)

    def test_slow_stream_respects_total_deadline_and_cancellation(self):
        import subprocess,sys,tempfile,time
        from pathlib import Path
        code="""
import http.client,pathlib,socket,sys,threading,time
from unittest.mock import patch
from runner.source_fetch import SourceFetcher
class Connection:
 def __init__(self): self.client,self.server=socket.socketpair(); self.client.settimeout(1)
 def request(self,*args,**kwargs):
  def send():
   try:
    self.server.sendall(b'HTTP/1.1 200 OK\\r\\nContent-Type: text/plain\\r\\nContent-Length: 5\\r\\n\\r\\n')
    self.server.sendall(b'h'); pathlib.Path(sys.argv[1]).write_text('streaming')
    for value in b'ello': time.sleep(.6); self.server.sendall(bytes([value]))
   except OSError: pass
  threading.Thread(target=send,daemon=True).start()
 def getresponse(self):
  response=http.client.HTTPResponse(self.client); response.begin(); return response
 def close(self): self.client.close(); self.server.close()
f=SourceFetcher(['https://docs.example.com/'],timeout=1); f._connection=lambda *_:Connection()
with patch('runner.source_fetch.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]): f._fetch_direct('https://docs.example.com/')
"""
        real_popen=subprocess.Popen
        with tempfile.TemporaryDirectory() as root:
            for cancel_on_stream in (False,True):
                marker=Path(root)/str(cancel_on_stream); fetcher=SourceFetcher(['https://docs.example.com/'],timeout=1 if not cancel_on_stream else 5); children=[]; cancellations=[]; started=time.monotonic()
                def launch(*args,**kwargs):
                    if cancel_on_stream: time.sleep(2.05)
                    child=real_popen([sys.executable,'-c',code,str(marker)],**kwargs); children.append(child); return child
                def cancelled():
                    if cancel_on_stream and marker.exists(): cancellations.append(time.monotonic()); return True
                    return False
                with self.subTest(cancel_on_stream=cancel_on_stream),patch('runner.source_fetch.subprocess.Popen',side_effect=launch):
                    with self.assertRaises(SourceFetchError):
                        fetcher.fetch('https://docs.example.com/',cancelled=cancelled)
                    self.assertTrue(marker.exists())
                    if cancel_on_stream:
                        self.assertTrue(cancellations)
                        self.assertLess(time.monotonic()-cancellations[0],1.6)
                    else: self.assertLess(time.monotonic()-started,1.6)
                    self.assertEqual(len(children),1); self.assertIsNotNone(children[0].poll())
    def test_public_json_sources_must_be_valid_and_bounded(self):
        fetcher=SourceFetcher(['https://docs.example.com/data'],max_bytes=32)
        address=[(2,1,6,'',('93.184.216.34',443))]
        for body,valid in ((b'{"rule":"9:16"}',True),(b'{bad',False),(b'{"n":NaN}',False)):
            with self.subTest(body=body),patch('runner.source_fetch.socket.getaddrinfo',return_value=address),patch.object(fetcher,'_connection',return_value=Connection(Response(body=body,content_type='application/json'))):
                if valid:self.assertEqual(fetcher._fetch_direct('https://docs.example.com/data')['content'],body.decode())
                else:
                    with self.assertRaises(SourceFetchError):fetcher._fetch_direct('https://docs.example.com/data')
    def test_fetch_uses_exact_approved_public_host_and_records_digest(self):
        import hashlib
        connection=Connection(Response()); fetcher=SourceFetcher(['https://docs.example.com/path?q=1'])
        addresses=[(2,1,6,'',('93.184.216.34',443))]
        with patch('runner.source_fetch.socket.getaddrinfo',return_value=addresses),patch.object(fetcher,'_connection',return_value=connection) as factory:
            result=fetcher._fetch_direct('https://docs.example.com/path?q=1')
        self.assertEqual(result['content'],'primary evidence')
        self.assertEqual(result['sha256'],hashlib.sha256(b'primary evidence').hexdigest())
        self.assertEqual(factory.call_args.args,('docs.example.com',['93.184.216.34']))
        self.assertEqual(connection.requests[0][0],('GET','/path?q=1'))
        self.assertTrue(connection.closed)
    def test_connection_failure_falls_back_to_next_validated_address_only(self):
        import socket, ssl
        from runner.source_fetch import _PinnedHTTPS
        class Sock:
            closed=False
            def settimeout(self,value): pass
            def close(self): self.closed=True
        attempts=[]
        def create(address,timeout):
            attempts.append(address[0])
            if address[0]=='93.184.216.34': raise ConnectionRefusedError()
            return Sock()
        connection=_PinnedHTTPS('docs.example.com',['93.184.216.34','93.184.216.35'],5)
        with patch('runner.source_fetch.socket.create_connection',side_effect=create),patch.object(connection._context,'wrap_socket',side_effect=lambda sock,server_hostname: sock):
            connection.connect()
        self.assertEqual(attempts,['93.184.216.34','93.184.216.35'])
        # Certificate verification failures never fall back to another address.
        attempts.clear(); socks=[]
        def create_ok(address,timeout):
            attempts.append(address[0]); socks.append(Sock()); return socks[-1]
        connection=_PinnedHTTPS('docs.example.com',['93.184.216.34','93.184.216.35'],5)
        with patch('runner.source_fetch.socket.create_connection',side_effect=create_ok),patch.object(connection._context,'wrap_socket',side_effect=ssl.SSLCertVerificationError()):
            with self.assertRaises(ssl.SSLCertVerificationError): connection.connect()
        self.assertEqual(attempts,['93.184.216.34']); self.assertTrue(socks[0].closed)
        # Handshake resets do fall back, and all-address failure surfaces the error.
        attempts.clear()
        connection=_PinnedHTTPS('docs.example.com',['93.184.216.34','93.184.216.35'],5)
        with patch('runner.source_fetch.socket.create_connection',side_effect=create_ok),patch.object(connection._context,'wrap_socket',side_effect=ConnectionResetError()):
            with self.assertRaises(ConnectionResetError): connection.connect()
        self.assertEqual(attempts,['93.184.216.34','93.184.216.35'])

    def test_fallback_cannot_exceed_total_deadline(self):
        from runner.source_fetch import _PinnedHTTPS
        attempts=[]
        def create(address,timeout):
            attempts.append(address[0]); connection.deadline=0; raise TimeoutError()
        connection=_PinnedHTTPS('docs.example.com',['93.184.216.34','93.184.216.35'],5)
        with patch('runner.source_fetch.socket.create_connection',side_effect=create):
            with self.assertRaises(TimeoutError): connection.connect()
        self.assertEqual(attempts,['93.184.216.34'])

    def test_slow_connect_leaves_handshake_only_the_remaining_deadline(self):
        from runner.source_fetch import _PinnedHTTPS
        class Sock:
            timeouts=[]
            def settimeout(self,value): self.timeouts.append(value)
            def close(self): pass
        connection=_PinnedHTTPS('docs.example.com',['93.184.216.34'],5)
        def create(address,timeout): connection.deadline-=4.5; return Sock()
        with patch('runner.source_fetch.socket.create_connection',side_effect=create),patch.object(connection._context,'wrap_socket',side_effect=lambda sock,server_hostname: sock):
            connection.connect()
        self.assertLessEqual(Sock.timeouts[0],0.6)
        connection=_PinnedHTTPS('docs.example.com',['93.184.216.34'],5)
        def expire(address,timeout): connection.deadline=0; return Sock()
        with patch('runner.source_fetch.socket.create_connection',side_effect=expire):
            with self.assertRaises(TimeoutError): connection.connect()

    def test_fetch_forwards_all_validated_addresses(self):
        fetcher=SourceFetcher(['https://docs.example.com/'])
        addresses=[(2,1,6,'',('93.184.216.34',443)),(2,1,6,'',('93.184.216.35',443))]
        with patch('runner.source_fetch.socket.getaddrinfo',return_value=addresses),patch.object(fetcher,'_connection',return_value=Connection(Response())) as factory:
            fetcher._fetch_direct('https://docs.example.com/')
        self.assertEqual(factory.call_args.args,('docs.example.com',['93.184.216.34','93.184.216.35']))

    def test_rejects_unapproved_urls_and_private_dns_before_connecting(self):
        fetcher=SourceFetcher(['https://docs.example.com/'])
        for url in ('http://docs.example.com/','https://evil.example.com/','https://u:p@docs.example.com/','https://docs.example.com:444/','https://127.0.0.1/','https://docs.example.com/?private=data'):
            with self.subTest(url=url),self.assertRaises(SourceFetchError): fetcher._fetch_direct(url)
        for addresses in ([('127.0.0.1',443)],[('93.184.216.34',443),('10.0.0.1',443)]):
            entries=[(2,1,6,'',(ip,port)) for ip,port in addresses]
            with self.subTest(addresses=addresses),patch('runner.source_fetch.socket.getaddrinfo',return_value=entries),patch.object(fetcher,'_connection') as connect:
                with self.assertRaises(SourceFetchError): fetcher._fetch_direct('https://docs.example.com/')
                connect.assert_not_called()
    def test_rejects_redirects_oversized_and_nontext_responses(self):
        fetcher=SourceFetcher(['https://docs.example.com/'],max_bytes=16)
        for response in (Response(302),Response(body=b'x'*17),Response(content_type='application/pdf'),Response(body=b'\xff'),Response(encoding='gzip')):
            with self.subTest(response=response),patch('runner.source_fetch.socket.getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))]),patch.object(fetcher,'_connection',return_value=Connection(response)):
                with self.assertRaises(SourceFetchError): fetcher._fetch_direct('https://docs.example.com/')
