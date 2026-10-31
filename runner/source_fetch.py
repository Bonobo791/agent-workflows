"""Bounded HTTPS retrieval for explicitly approved public source hosts."""
import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit

class SourceFetchError(RuntimeError): pass

class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,address,timeout):
        context=ssl.create_default_context();context.check_hostname=True;context.verify_mode=ssl.CERT_REQUIRED
        super().__init__(host,port=443,timeout=timeout,context=context)
        self.address=address
    def connect(self):
        sock=socket.create_connection((self.address,443),self.timeout)
        try: self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except Exception: sock.close(); raise

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
    def _connection(self,host,address): return _PinnedHTTPS(host,address,self.timeout)
    def fetch(self,url):
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
            connection=self._connection(host,ips[0])
            path=parsed.path or '/'
            if parsed.query: path+='?'+parsed.query
            connection.request('GET',path,headers={'Accept':'text/html, text/plain, application/xhtml+xml','Accept-Encoding':'identity','User-Agent':'agent-workflows-source-check/1'})
            response=connection.getresponse()
            content_type=response.getheader('Content-Type','').split(';',1)[0].strip().lower()
            encoding=response.getheader('Content-Encoding','identity').strip().lower()
            if response.status!=200 or encoding not in ('','identity') or content_type not in ('text/html','text/plain','application/xhtml+xml'): raise ValueError('source response unavailable')
            data=response.read(self.max_bytes+1)
            if len(data)>self.max_bytes or not data: raise ValueError('source response size invalid')
            content=data.decode('utf-8','strict')
            return {'url':url,'content_type':content_type,'sha256':hashlib.sha256(data).hexdigest(),'content':content}
        except (OSError,ValueError,TypeError,UnicodeError,http.client.HTTPException): raise SourceFetchError('source retrieval failed or was disallowed') from None
        finally:
            if connection is not None: connection.close()
