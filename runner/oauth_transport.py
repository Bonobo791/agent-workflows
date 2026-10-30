"""Fixed-origin OAuth transport. Calls are made only by an explicit caller."""
import json
import urllib.error
import urllib.parse
import urllib.request

ISSUER='https://auth.openai.com'
DISCOVERY=ISSUER+'/.well-known/openid-configuration'
TOKEN=ISSUER+'/api/accounts/oauth/token'

class TransportError(RuntimeError): pass

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class HTTPS:
    def __init__(self): self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    def request(self,method,url,body=None,headers=None):
        try:
            request=urllib.request.Request(url,data=body,headers=headers or {},method=method)
            with self.opener.open(request,timeout=15) as response:
                if response.status!=200: raise TransportError('unexpected HTTP status')
                data=response.read(1048577)
                if len(data)>1048576: raise TransportError('response too large')
                result=json.loads(data)
                if not isinstance(result,dict): raise TransportError('invalid response')
                return result
        except (OSError,ValueError,urllib.error.URLError): raise TransportError('OAuth request failed') from None

class OAuthTransport:
    def __init__(self,http=None): self.http=http or HTTPS()
    def post_form(self,form):
        if not isinstance(form,dict) or form.get('grant_type') not in ('authorization_code','refresh_token'): raise TransportError('unsupported grant')
        body=urllib.parse.urlencode(form).encode('ascii')
        return self.http.request('POST',TOKEN,body,{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'})
    def jwks(self):
        metadata=self.http.request('GET',DISCOVERY,headers={'Accept':'application/json'})
        if metadata.get('issuer')!=ISSUER: raise TransportError('issuer mismatch')
        url=metadata.get('jwks_uri','')
        try: parts=urllib.parse.urlsplit(url)
        except ValueError: raise TransportError('invalid key URL') from None
        if parts.scheme!='https' or parts.netloc!='auth.openai.com' or not parts.path.startswith('/') or parts.query or parts.fragment: raise TransportError('untrusted key URL')
        keys=self.http.request('GET',url,headers={'Accept':'application/json'})
        if not isinstance(keys.get('keys'),list): raise TransportError('invalid key set')
        return keys
