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
    def test_fetch_uses_exact_approved_public_host_and_records_digest(self):
        import hashlib
        connection=Connection(Response()); fetcher=SourceFetcher(['https://docs.example.com/path?q=1'])
        addresses=[(2,1,6,'',('93.184.216.34',443))]
        with patch('runner.source_fetch.socket.getaddrinfo',return_value=addresses),patch.object(fetcher,'_connection',return_value=connection) as factory:
            result=fetcher.fetch('https://docs.example.com/path?q=1')
        self.assertEqual(result['content'],'primary evidence')
        self.assertEqual(result['sha256'],hashlib.sha256(b'primary evidence').hexdigest())
        self.assertEqual(factory.call_args.args,('docs.example.com','93.184.216.34'))
        self.assertEqual(connection.requests[0][0],('GET','/path?q=1'))
        self.assertTrue(connection.closed)
    def test_rejects_unapproved_urls_and_private_dns_before_connecting(self):
        fetcher=SourceFetcher(['https://docs.example.com/'])
        for url in ('http://docs.example.com/','https://evil.example.com/','https://u:p@docs.example.com/','https://docs.example.com:444/','https://127.0.0.1/','https://docs.example.com/?private=data'):
            with self.subTest(url=url),self.assertRaises(SourceFetchError): fetcher.fetch(url)
        for addresses in ([('127.0.0.1',443)],[('93.184.216.34',443),('10.0.0.1',443)]):
            entries=[(2,1,6,'',(ip,port)) for ip,port in addresses]
            with self.subTest(addresses=addresses),patch('runner.source_fetch.socket.getaddrinfo',return_value=entries),patch.object(fetcher,'_connection') as connect:
                with self.assertRaises(SourceFetchError): fetcher.fetch('https://docs.example.com/')
                connect.assert_not_called()
    def test_rejects_redirects_oversized_and_nontext_responses(self):
        fetcher=SourceFetcher(['https://docs.example.com/'],max_bytes=16)
        for response in (Response(302),Response(body=b'x'*17),Response(content_type='application/pdf'),Response(body=b'\xff'),Response(encoding='gzip')):
            with self.subTest(response=response),patch('runner.source_fetch.socket.getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))]),patch.object(fetcher,'_connection',return_value=Connection(response)):
                with self.assertRaises(SourceFetchError): fetcher.fetch('https://docs.example.com/')
