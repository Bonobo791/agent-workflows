"""Allowlisted Coolify service-application lifecycle requests.

A 200 only confirms that Coolify accepted the request; it never proves that an
incarnation stopped. Do not place this controller or its token in job sandboxes.
"""
import re
import urllib.parse
import urllib.request
from runner.oauth_transport import _NoRedirect

class CoolifyError(RuntimeError): pass

class HTTPS:
    def __init__(self): self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    def post(self,url,token):
        request=urllib.request.Request(url,data=b'',method='POST',headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
        try:
            with self.opener.open(request,timeout=15) as response:
                if len(response.read(65537))>65536: raise CoolifyError('response too large')
                return response.status
        except Exception: raise CoolifyError('lifecycle request outcome uncertain') from None

class CoolifyController:
    def __init__(self,origin,token,service,application,http=None):
        parsed=urllib.parse.urlsplit(origin)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment: raise CoolifyError('trusted HTTPS origin required')
        if not all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',v) for v in (service,application)): raise CoolifyError('invalid target')
        if not isinstance(token,str) or not token or '\r' in token or '\n' in token: raise CoolifyError('controller credential required')
        self.origin=origin.rstrip('/'); self.token=token; self.service=service; self.application=application; self.http=http or HTTPS()
    def _request(self,target,action):
        if not isinstance(target,dict) or target.get('origin',self.origin).rstrip('/')!=self.origin or target.get('service')!=self.service or target.get('application')!=self.application: raise CoolifyError('target not allowlisted')
        url=self.origin+'/api/v1/services/'+self.service+'/applications/'+self.application+'/'+action
        try: status=self.http.post(url,self.token)
        except Exception: raise CoolifyError('lifecycle request outcome uncertain') from None
        if status!=200: raise CoolifyError('lifecycle request outcome uncertain')
    def stop(self,target): self._request(target,'stop')
    def restart(self,target): self._request(target,'restart')
