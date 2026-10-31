"""Isolated, bounded tracker request; credentials enter only through stdin."""
import base64
import json
import math
import signal
import sys
import urllib.parse
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def main():
    data=json.load(sys.stdin)
    parsed=urllib.parse.urlsplit(data['endpoint'])
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/')!='/mcp': raise ValueError()
    timeout=data['timeout']
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<=0: raise ValueError()
    signal.signal(signal.SIGALRM,signal.SIG_DFL); signal.setitimer(signal.ITIMER_REAL,timeout)
    payload=json.dumps(data['message'],separators=(',',':')).encode()
    headers={**data['headers'],'Authorization':'Bearer '+data['token'],'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
    request=urllib.request.Request(data['endpoint'],data=payload,method='POST',headers=headers)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=timeout) as response:
        body=response.read(2097153)
        if len(body)>2097152: raise ValueError()
        headers={key:response.headers[key] for key in ('Content-Type','Mcp-Session-Id') if key in response.headers}
        json.dump({'status':response.status,'headers':headers,'body':base64.b64encode(body).decode()},sys.stdout,separators=(',',':'))

if __name__=='__main__':
    try: main()
    except Exception: sys.exit(1)
