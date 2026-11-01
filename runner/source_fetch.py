"""Bounded HTTPS retrieval for explicitly approved public source hosts."""
import hashlib
import http.client
import ipaddress
import json
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import urlsplit

class SourceFetchError(RuntimeError): pass

CONTENT_TYPES=frozenset(('text/html','text/plain','application/xhtml+xml','application/json'))

def validate_content(content_type,data):
    if content_type not in CONTENT_TYPES or not isinstance(data,bytes) or not data: raise ValueError('invalid source content')
    text=data.decode('utf-8','strict')
    if content_type=='application/json':
        def invalid(_): raise ValueError('invalid JSON constant')
        try: json.loads(text,parse_constant=invalid)
        except (ValueError,RecursionError): raise ValueError('invalid JSON source') from None
    return text

class _PinnedHTTPS(http.client.HTTPSConnection):
    """Connect only to pre-validated public addresses; try the next on connection failure, within one total deadline."""
    def __init__(self,host,addresses,timeout):
        context=ssl.create_default_context();context.check_hostname=True;context.verify_mode=ssl.CERT_REQUIRED
        super().__init__(host,port=443,timeout=timeout,context=context)
        self.addresses=list(addresses)
        if not self.addresses: raise ValueError('no validated addresses')
        self.deadline=time.monotonic()+timeout
    def connect(self):
        failure=None
        for address in self.addresses:
            remaining=self.deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('source connection deadline exceeded')
            try: sock=socket.create_connection((address,443),min(self.timeout,remaining))
            except OSError as exc: failure=exc; continue
            try:
                self.sock=self._context.wrap_socket(sock,server_hostname=self.host); return
            except ssl.SSLCertVerificationError: sock.close(); raise
            except OSError as exc: sock.close(); failure=exc
        raise failure

class SourceFetcher:
    def __init__(self,urls,*,max_bytes=524288,timeout=10):
        if not isinstance(urls,list) or not urls or len(urls)>100 or any(not isinstance(url,str) for url in urls) or len(set(urls))!=len(urls) or type(max_bytes) is not int or not 1<=max_bytes<=1048576 or type(timeout) is not int or not 1<=timeout<=30: raise SourceFetchError('invalid source fetch policy')
        hosts=set()
        for url in urls:
            try:
                parsed=urlsplit(url);host=parsed.hostname
                if not 1<=len(url)<=4096 or any(ord(c)<32 or ord(c)==127 for c in url) or parsed.scheme!='https' or not host or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443) or parsed.fragment: raise ValueError()
            except ValueError: raise SourceFetchError('invalid approved source URL') from None
            if not isinstance(host,str) or len(host)>253 or host!=host.lower() or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+',host): raise SourceFetchError('invalid source host')
            try: ipaddress.ip_address(host)
            except ValueError: hosts.add(host);continue
            raise SourceFetchError('literal IP hosts are forbidden')
        self.urls=frozenset(urls);self.hosts=frozenset(hosts);self.max_bytes=max_bytes;self.timeout=timeout
    def _connection(self,host,addresses): return _PinnedHTTPS(host,addresses,self.timeout)
    def fetch(self,url,*,cancelled=lambda:False):
        process=None
        try:
            if not isinstance(url,str) or url not in self.urls or not callable(cancelled): raise ValueError('unapproved URL')
            if cancelled(): raise ValueError('source request cancelled')
            deadline=time.monotonic()+self.timeout
            request=json.dumps({'url':url,'max_bytes':self.max_bytes,'timeout':self.timeout},separators=(',',':')).encode()
            process=subprocess.Popen([sys.executable,'-I',__file__,'--fetch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            pending=request
            while True:
                if cancelled(): raise ValueError('source request cancelled')
                remaining=deadline-time.monotonic()
                if remaining<=0: raise TimeoutError()
                try:
                    output,_=process.communicate(input=pending,timeout=min(.1,remaining)); break
                except subprocess.TimeoutExpired:
                    pending=None
            if cancelled() or time.monotonic()>deadline or process.returncode!=0 or len(output)>self.max_bytes*6+65536: raise ValueError('source request unavailable')
            result=json.loads(output)
            if not isinstance(result,dict) or set(result)!={'url','content_type','sha256','content'} or result['url']!=url or not isinstance(result['content'],str): raise ValueError('invalid source result')
            content=result['content'].encode('utf-8')
            if not content or len(content)>self.max_bytes or hashlib.sha256(content).hexdigest()!=result['sha256']: raise ValueError('invalid source result')
            validate_content(result['content_type'],content)
            if cancelled() or time.monotonic()>deadline: raise ValueError('source request expired')
            return result
        except Exception: raise SourceFetchError('source retrieval failed, timed out or was cancelled') from None
        finally:
            if process is not None and process.poll() is None:
                try: process.kill()
                except ProcessLookupError: pass
                process.communicate()

    def _fetch_direct(self,url):
        connection=None
        try:
            if not isinstance(url,str) or url not in self.urls: raise ValueError('unapproved URL')
            parsed=urlsplit(url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.hostname not in self.hosts or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443) or parsed.fragment: raise ValueError('unapproved URL')
            host=parsed.hostname
            addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
            if not addresses: raise ValueError('source DNS unavailable')
            ips=[]
            for family,kind,_,_,address in addresses:
                if family not in (socket.AF_INET,socket.AF_INET6) or kind!=socket.SOCK_STREAM: raise ValueError('invalid DNS result')
                ip=ipaddress.ip_address(address[0]); mapped=getattr(ip,'ipv4_mapped',None)
                if not ip.is_global or (mapped is not None and not mapped.is_global): raise ValueError('nonpublic DNS result')
                ips.append(str(ip))
            connection=self._connection(host,ips)
            path=parsed.path or '/'
            if parsed.query: path+='?'+parsed.query
            connection.request('GET',path,headers={'Accept':'text/html, text/plain, application/xhtml+xml, application/json','Accept-Encoding':'identity','User-Agent':'agent-workflows-source-check/1'})
            response=connection.getresponse()
            content_type=response.getheader('Content-Type','').split(';',1)[0].strip().lower()
            encoding=response.getheader('Content-Encoding','identity').strip().lower()
            if response.status!=200 or encoding not in ('','identity') or content_type not in CONTENT_TYPES: raise ValueError('source response unavailable')
            data=response.read(self.max_bytes+1)
            if len(data)>self.max_bytes or not data: raise ValueError('source response size invalid')
            content=validate_content(content_type,data)
            return {'url':url,'content_type':content_type,'sha256':hashlib.sha256(data).hexdigest(),'content':content}
        except (OSError,ValueError,TypeError,UnicodeError,http.client.HTTPException): raise SourceFetchError('source retrieval failed or was disallowed') from None
        finally:
            if connection is not None: connection.close()


if __name__=='__main__':
    try:
        if sys.argv[1:]!=['--fetch']: raise ValueError()
        request=json.loads(sys.stdin.buffer.read(65537))
        if not isinstance(request,dict) or set(request)!={'url','max_bytes','timeout'}: raise ValueError()
        fetcher=SourceFetcher([request['url']],max_bytes=request['max_bytes'],timeout=request['timeout'])
        signal.signal(signal.SIGALRM,signal.SIG_DFL); signal.setitimer(signal.ITIMER_REAL,fetcher.timeout)
        result=fetcher._fetch_direct(request['url'])
        sys.stdout.write(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    except Exception: sys.exit(1)
