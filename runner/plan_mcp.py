"""Read-only It's a Plan Streamable HTTP MCP client.

Configure the HTTPS endpoint and secret at runtime. No network call occurs on
construction. Result data is untrusted and readiness still requires policy and
repository checks. The client never calls write tools.
"""
import json
import re
import urllib.parse
import urllib.request
from runner.oauth_transport import _NoRedirect

class MCPError(RuntimeError): pass

class HTTPSMCP:
    def __init__(self,endpoint,token):
        parsed=urllib.parse.urlsplit(endpoint)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/')!='/mcp': raise MCPError('HTTPS MCP endpoint required')
        if not isinstance(token,str) or not token or '\n' in token or '\r' in token: raise MCPError('credential required')
        self.endpoint,self.token=endpoint,token
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    def post(self,message,headers):
        try:
            payload=json.dumps(message,separators=(',',':')).encode()
            request=urllib.request.Request(self.endpoint,data=payload,method='POST',headers={**headers,'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
            with self.opener.open(request,timeout=30) as response:
                body=response.read(2097153)
                if len(body)>2097152: raise MCPError('response too large')
                return response.status,response.headers,body
        except Exception: raise MCPError('MCP request failed') from None

class PlanMCP:
    ALLOWED={'get_issue_by_number','get_issue','list_attachments','list_issues','list_issue_activity'}
    def __init__(self,transport): self.transport=transport; self.session=None; self.ready=False; self.next_id=0
    def _headers(self):
        headers={}
        if self.ready: headers['MCP-Protocol-Version']='2025-06-18'
        if self.session: headers['MCP-Session-Id']=self.session
        return headers
    def _post(self,message,notification=False):
        status,headers,body=self.transport.post(message,self._headers())
        session=headers.get('Mcp-Session-Id') or headers.get('mcp-session-id')
        if session:
            if self.session and self.session!=session: raise MCPError('session changed')
            self.session=session
        if notification:
            if status!=202: raise MCPError('notification rejected')
            return None
        if status!=200: raise MCPError('MCP request failed')
        kind=(headers.get('Content-Type') or headers.get('content-type') or '').split(';',1)[0].strip().lower()
        try:
            if kind=='application/json': messages=[json.loads(body)]
            elif kind=='text/event-stream':
                messages=[]
                for block in body.decode('utf-8').replace('\r\n','\n').split('\n\n'):
                    data='\n'.join(line[5:].lstrip(' ') for line in block.splitlines() if line.startswith('data:'))
                    if data: messages.append(json.loads(data))
            else: raise MCPError('unsupported MCP response')
            if any('method' in m and 'id' in m for m in messages): raise MCPError('server requests unsupported')
            matches=[m for m in messages if m.get('id')==message['id'] and 'method' not in m]
            if len(matches)!=1 or 'error' in matches[0] or not isinstance(matches[0].get('result'),dict): raise MCPError('invalid MCP response')
            return matches[0]['result']
        except (ValueError,TypeError,UnicodeError,AttributeError): raise MCPError('invalid MCP response') from None
    def initialize(self):
        self.next_id=1
        response=self._post({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'agent_workflows','version':'0.1.0'}}})
        if response.get('protocolVersion')!='2025-06-18' or 'tools' not in response.get('capabilities',{}): raise MCPError('unsupported MCP capabilities')
        self.ready=True
        try: self._post({'jsonrpc':'2.0','method':'notifications/initialized'},notification=True)
        except Exception: self.ready=False; raise
    def call(self,name,arguments):
        if name not in self.ALLOWED or not isinstance(arguments,dict): raise MCPError('tool unavailable')
        if not self.ready: self.initialize()
        self.next_id+=1
        result=self._post({'jsonrpc':'2.0','id':self.next_id,'method':'tools/call','params':{'name':name,'arguments':arguments}})
        if result.get('isError'): raise MCPError('tool failed')
        structured=result.get('structuredContent')
        if not isinstance(structured,dict) or structured.get('ok') is not True: raise MCPError('invalid tool result')
        return structured
    def get_issue(self,project_key,number):
        if not isinstance(project_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?',project_key) or type(number) is not int or number<1: raise MCPError('invalid issue identifier')
        return self.call('get_issue_by_number',{'projectKey':project_key,'sequenceNumber':number})
