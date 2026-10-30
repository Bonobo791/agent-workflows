"""Fixed, one-shot GitHub mutation helper; credentials remain in memory.

This process must run only in a dedicated, isolated non-model container. Its
stdin is a private Engine attach stream owned by the trusted controller.
"""
import json
import re
import sys
from workflows.engineering.github import HTTPSGitHub

LIMIT=262144

def execute(message, factory=HTTPSGitHub):
    if not isinstance(message,dict) or set(message)!={'credential','kind','repo','payload'}: raise ValueError('invalid request')
    token=message['credential']; repo=message['repo']; payload=message['payload']; kind=message['kind']
    if not isinstance(token,str) or not token or len(token)>8192 or any(c in token for c in '\r\n\0'): raise ValueError('credential unavailable')
    if not isinstance(repo,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not isinstance(payload,dict): raise ValueError('invalid request')
    http=factory(token)
    if kind=='draft':
        if set(payload)!={'head','base','title','body','draft'} or payload['draft'] is not True or any(not isinstance(payload[k],str) or not payload[k] for k in ('head','base','title')) or not isinstance(payload['body'],str): raise ValueError('invalid draft')
        result=http.request('POST','/repos/'+repo+'/pulls',payload)
        number=result.get('number'); sha=result.get('head',{}).get('sha')
        if type(number) is not int or number<1 or result.get('draft') is not True or not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',sha): raise ValueError('invalid response')
        return {'number':number,'draft':True,'head':{'sha':sha}}
    if kind=='ready':
        if set(payload)!={'node'} or not isinstance(payload['node'],str) or not payload['node']: raise ValueError('invalid readiness')
        result=http.mark_ready(payload['node'])
        if result.get('id')!=payload['node'] or result.get('isDraft') is not False: raise ValueError('invalid response')
        return {'id':payload['node'],'isDraft':False}
    raise ValueError('unsupported operation')

def main():
    try:
        raw=sys.stdin.buffer.readline(LIMIT+1)
        if len(raw)>LIMIT or not raw.endswith(b'\n'): raise ValueError('invalid request')
        message=json.loads(raw); result=execute(message)
        sys.stdout.write(json.dumps({'ok':True,'result':result},separators=(',',':'))+'\n'); sys.stdout.flush()
        return 0
    except Exception:
        sys.stdout.write('{"ok":false}\n'); sys.stdout.flush()
        return 1

if __name__=='__main__': raise SystemExit(main())
