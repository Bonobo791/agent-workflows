import json
import unittest
from runner.plan_mcp import PlanMCP, MCPError

class FakeTransport:
    def __init__(self): self.calls=[]
    def post(self,message,headers):
        self.calls.append((message,headers))
        if message['method']=='initialize': return (200,{'Content-Type':'application/json','Mcp-Session-Id':'session'},json.dumps({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}}).encode())
        if message['method']=='notifications/initialized': return (202,{},b'')
        return (200,{'Content-Type':'text/event-stream'},('event: message\ndata: '+json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'structuredContent':{'ok':True,'data':{'identifier':'TEST-1'}}}})+'\n\n').encode())

class MCPTests(unittest.TestCase):
    def test_team_scoped_project_key_is_sent_without_rewriting(self):
        transport=FakeTransport(); client=PlanMCP(transport)
        client.get_issue('moderaty.MC',32)
        params=transport.calls[-1][0]['params']
        self.assertEqual(params['name'],'get_issue_by_number')
        self.assertEqual(params['arguments'],{'projectKey':'moderaty.MC','sequenceNumber':32})

    def test_malformed_project_keys_do_not_reach_transport(self):
        for key in ('team..MC','.MC','team.','team.MC.extra','team/MC'):
            transport=FakeTransport(); client=PlanMCP(transport)
            with self.subTest(key=key),self.assertRaises(MCPError): client.get_issue(key,1)
            self.assertEqual(transport.calls,[])

    def test_handshake_and_read_only_tool_call(self):
        transport=FakeTransport(); client=PlanMCP(transport)
        result=client.get_issue('TEST',1)
        self.assertEqual(result['data']['identifier'],'TEST-1')
        self.assertEqual(transport.calls[-1][1]['MCP-Session-Id'],'session')
        self.assertEqual(transport.calls[-1][1]['MCP-Protocol-Version'],'2025-06-18')
    def test_write_tools_cannot_be_called(self):
        client=PlanMCP(FakeTransport())
        with self.assertRaises(MCPError): client.call('update_issue',{})
