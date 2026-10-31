"""Isolated, bounded tracker request; credentials enter only through stdin."""
import base64
import json
import sys
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def main():
    data=json.load(sys.stdin)
    payload=json.dumps(data['message'],separators=(',',':')).encode()
    headers={**data['headers'],'Authorization':'Bearer '+data['token'],'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
    request=urllib.request.Request(data['endpoint'],data=payload,method='POST',headers=headers)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=30) as response:
        body=response.read(2097153)
        if len(body)>2097152: raise ValueError()
        headers={key:response.headers[key] for key in ('Content-Type','Mcp-Session-Id') if key in response.headers}
        json.dump({'status':response.status,'headers':headers,'body':base64.b64encode(body).decode()},sys.stdout,separators=(',',':'))

if __name__=='__main__':
    try: main()
    except Exception: sys.exit(1)
