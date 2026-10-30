import hashlib
import http.client
import http.server
import json
import socketserver
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from runner.worker import build_control
from runner.manual_lifecycle import ManualLifecycle
from tests.source_fixture import REGISTER, verifier as fixture_verifier

class StageEntrypointTests(unittest.TestCase):
    def test_marketing_correction_fanout_and_human_gate_over_fake_protocols(self):
        class External(http.server.BaseHTTPRequestHandler):
            version='v1'; owner=None; available=True
            def log_message(self,*args): pass
            def do_GET(self): self.reply([])
            def do_POST(self):
                message=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if message['method']=='notifications/initialized': self.send_response(202); self.end_headers(); return
                if message['method']=='initialize': result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}}}
                else:
                    name=message['params']['name']
                    if name=='get_issue_by_number': data={'id':1,'identifier':'DEMO-1','title':'Synthetic','description':'Example','columnId':10,'archivedAt':None,'assigneeUserId':External.owner,'delegateUserId':None,'dueDate':None,'startDate':None,'subtasks':[],'links':[],'development':[],'updatedAt':External.version}
                    elif name=='list_issue_activity': data={'items':[],'nextCursor':None}
                    else: data=[]
                    if name=='get_issue_by_number' and not External.available: data=None
                    result={'structuredContent':{'ok':True,'data':data}}
                self.reply({'jsonrpc':'2.0','id':message['id'],'result':result})
            def reply(self,data):
                payload=json.dumps(data).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
        class Engine(socketserver.StreamRequestHandler):
            containers={}; specs=[]; canonical_reviews=0; source_reviews=0; resumed=[]
            def handle(self):
                request=self.rfile.readline().decode().split(); headers={}
                while True:
                    line=self.rfile.readline()
                    if line==b'\r\n': break
                    key,value=line.decode().split(':',1); headers[key.lower()]=value.strip()
                path=request[1]; body=self.rfile.read(int(headers.get('content-length','0')))
                def reply(status,data=None):
                    payload=json.dumps(data).encode() if data is not None else b''
                    self.wfile.write(('HTTP/1.1 '+str(status)+' OK\r\nContent-Length: '+str(len(payload))+'\r\nContent-Type: application/json\r\n\r\n').encode()+payload); self.wfile.flush()
                if path.endswith('/containers/create'):
                    spec=json.loads(body); cid=format(len(Engine.specs)+1,'064x'); Engine.specs.append(spec)
                    mounts={m['Target']:m['Source'] for m in spec['HostConfig']['Mounts']}
                    Engine.containers[cid]={'state':'created','label':spec['Labels']['agent-workflows.run'],'mounts':mounts}; reply(201,{'Id':cid}); return
                cid=path.split('/')[3]; item=Engine.containers[cid]
                if path.endswith('/json'): reply(200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run':item['label']}},'State':{'Running':item['state']=='running','Status':item['state']}})
                elif path.endswith('/start'): item['state']='running'; reply(204)
                elif '/stop?' in path: item['state']='exited'; reply(204)
                elif '/wait?' in path: reply(200,{'StatusCode':0})
                elif '/attach?' in path:
                    self.wfile.write(b'HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/vnd.docker.raw-stream\r\n\r\n'); self.wfile.flush()
                    def emit(value):
                        data=json.dumps(value).encode()+b'\n'; self.wfile.write(struct.pack('>BxxxI',1,len(data))+data); self.wfile.flush()
                    for line in self.rfile:
                        msg=json.loads(line); method=msg.get('method'); ident=msg.get('id')
                        if method=='initialize': emit({'id':ident,'result':{}})
                        elif method=='model/list': emit({'id':ident,'result':{'data':[{'model':'fake-terra','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None}})
                        elif method=='thread/start':
                            tid='thread-'+cid[-4:]; Path(item['mounts']['/codex-home'],'synthetic-state').write_text(tid); emit({'id':ident,'result':{'thread':{'id':tid}}})
                        elif method=='thread/resume':
                            tid=msg['params']['threadId']; assert Path(item['mounts']['/codex-home'],'synthetic-state').read_text()==tid
                            Engine.resumed.append(tid); emit({'id':ident,'result':{'thread':{'id':tid}}})
                        elif method=='turn/start':
                            tid=msg['params']['threadId']; payload=json.JSONDecoder().raw_decode(msg['params']['input'][0]['text'].split('\n',1)[1])[0]; stage=payload['stage']; refs=payload['input_versions']
                            if stage=='source_review':
                                Engine.source_reviews+=1
                                data=json.dumps({'register_ref':refs[0],'verifier_id':'synthetic-independent-verifier','checks':[{'claim_id':'claim-1','source_id':'source-1','evidence_id':'evidence-1','supported':True,'primary':True}],'draft_ref':refs[1] if len(refs)>1 else None,'omitted_claims':['unregistered material claim'] if Engine.source_reviews==2 else [],'coverage_complete':Engine.source_reviews!=2}).encode()
                                Path(item['mounts']['/output'],'verification.json').write_bytes(data)
                            else:
                                data=('synthetic '+stage+' '+cid[-4:]).encode(); Path(item['mounts']['/output'],'result.txt').write_bytes(data)
                            outputs=[hashlib.sha256(data).hexdigest()]
                            if stage=='brief':
                                source_data=json.dumps(REGISTER).encode(); Path(item['mounts']['/output'],'sources.json').write_bytes(source_data); outputs.append(hashlib.sha256(source_data).hexdigest())
                            status='passed'; findings=[]
                            if stage=='canonical_draft' and Engine.resumed:
                                assert 'source_review_judgment' in payload['artifacts']
                                assert 'unregistered material claim' in payload['artifacts']['source_review_judgment']
                            if stage=='canonical_review':
                                assert 'Independently review editorial quality' in msg['params']['input'][0]['text']
                                assert 'synthetic brief' in payload['artifacts']['brief_and_sources']
                                assert len(refs)==4 and 'source_register' in payload['artifacts'] and 'source_verification' in payload['artifacts']
                                Engine.canonical_reviews+=1
                            result={'stage':stage,'status':status,'input_versions':refs,'outputs':outputs,'findings':findings}
                            if status=='blocked': result['reason']='correction required'
                            emit({'id':ident,'result':{'turn':{'id':'turn-'+cid[-4:]}}})
                            if stage=='source_review':
                                emit({'method':'item/completed','params':{'threadId':tid,'turnId':'turn-'+cid[-4:],'item':{'type':'mcpToolCall','id':'evidence-1','server':'source-evidence','tool':'fetch_primary','status':'completed','arguments':{'url':'https://example.invalid/evidence'},'result':{'content':[{'type':'text','text':'fixture primary source'}]}}}})
                            emit({'method':'item/agentMessage/delta','params':{'threadId':tid,'turnId':'turn-'+cid[-4:],'delta':json.dumps(result)}})
                            emit({'method':'turn/completed','params':{'threadId':tid,'turn':{'id':'turn-'+cid[-4:],'status':'completed'}}}); break
                else: reply(404)
        class EngineServer(socketserver.ThreadingUnixStreamServer): daemon_threads=True
        class Delivery:
            def ready(self): return True
            def deliver(self,assignment,cid): return True
            def verify_home(self,path): return True
        external=http.server.ThreadingHTTPServer(('127.0.0.1',0),External); external_thread=threading.Thread(target=external.serve_forever); external_thread.start()
        self.addCleanup(external.server_close); self.addCleanup(external_thread.join); self.addCleanup(external.shutdown)
        host,port=external.server_address
        class PlanHTTP:
            def post(self,message,headers):
                conn=http.client.HTTPConnection(host,port,timeout=3); conn.request('POST','/mcp',json.dumps(message).encode(),{'Content-Type':'application/json'}); response=conn.getresponse(); body=response.read(); status=response.status; h=dict(response.getheaders()); conn.close(); return status,h,body
        class GithubHTTP:
            def request(self,method,path,body=None):
                conn=http.client.HTTPConnection(host,port,timeout=3); conn.request(method,path); response=conn.getresponse(); result=json.loads(response.read()); conn.close(); return result
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700); skillroot=Path(root)/'skills'; (skillroot/'role').mkdir(parents=True); (skillroot/'role'/'SKILL.md').write_text('Synthetic role')
            sock=str(Path(root)/'engine.sock'); engine=EngineServer(sock,Engine); engine_thread=threading.Thread(target=engine.serve_forever); engine_thread.start()
            docker={'socket':sock,'image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            task={'run':'run-1','workflow':'marketing','project':'DEMO','number':1,'task_format':'guide','ready_columns':[10],'completed_columns':[20],'blocker_direction':'inward','plan_endpoint':'https://plan.example.invalid/mcp','github_owner':'owner','github_repo':'repo','github_branch':'task-1'}
            roles=['brief','canonical_draft','canonical_review','source_review','asset_brief']+[c+s for c in ('linkedin','x','facebook','instagram','reddit') for s in ('_draft','_review')]
            with patch('runner.plan_mcp.HTTPSMCP',return_value=PlanHTTP()),patch('workflows.engineering.github.HTTPSGitHub',return_value=GithubHTTP()):
                service,control=build_control(root,'x'*40,docker_config=docker,task_config=task,plan_token='synthetic-plan',github_token='synthetic-github',role_config={'skill_roots':[str(skillroot)],'required_skills':{role:['role'] for role in roles}},lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
                try:
                    service.source_tool_authority=lambda events,register,draft: all(item.get('type')=='mcpToolCall' and item.get('server')=='source-evidence' and item.get('tool')=='fetch_primary' and item.get('status')=='completed' and item.get('arguments',{}).get('url')=='https://example.invalid/evidence' and item.get('result',{}).get('content',[{}])[0].get('text')=='fixture primary source' for item in events['items']); service.docker_supervisor.credential_delivery=Delivery(); service.docker_supervisor.provider_ready=lambda:True
                    service.approve('controller','docker_config',service.docker_supervisor.config.policy_hash,'operator')
                    control.handle('x'*40,{'action':'submit_configured'}); q=control.handle('x'*40,{'action':'manual_claim'})['claim']; g=control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
                    req={'action':'dispatch_stage','run':'run-1','queue_claim':q['token'],'graph_claim':g}
                    for _ in range(30):
                        result=control.handle('x'*40,req)
                        if result.get('status')=='awaiting_approval': control.handle('x'*40,{'action':'approve','run':'run-1','approval':'freeze','version':result['version']})
                        if result.get('status')=='awaiting_review': break
                    self.assertEqual(result['status'],'awaiting_review'); self.assertEqual(Engine.canonical_reviews,2); self.assertEqual(Engine.source_reviews,3); self.assertEqual(len(Engine.resumed),1)
                    self.assertEqual(service.graph.get('run-1','asset_production')['status'],'blocked')
                    package=json.loads(service.artifacts.get(service.graph.get('run-1','assembly')['version']))
                    self.assertIn('synthetic canonical_draft',package['stages']['canonical_draft']['contents'][0])
                    self.assertIn('synthetic brief',package['stages']['brief']['contents'][0])
                    self.assertEqual(package['stages']['brief']['source_verification']['verifier_id'],'synthetic-independent-verifier')
                    self.assertIn('claim-1',package['stages']['brief']['contents'][1])
                    self.assertIn('synthetic linkedin_draft',package['stages']['linkedin_draft']['contents'][0])
                    self.assertTrue(service.verify_terminal('run-1'))
                    version=service.graph.get('run-1','assembly')['version']
                    from runner.control import ControlError
                    decision={'action':'human_review_decision','run':'run-1','queue_claim':q['token'],'graph_claim':g,'version':'0'*64,'decision':'approved'}
                    with self.assertRaises(ControlError): control.handle('x'*40,decision)
                    decision['version']=version
                    self.assertEqual(control.handle('x'*40,decision)['decision'],'approved')
                    self.assertEqual(control.handle('x'*40,{'action':'human_review_status','run':'run-1'})['decision']['version'],version)
                    with self.assertRaises(ControlError): control.handle('x'*40,decision)
                    brief=package['stages']['brief']['artifact']; review=package['stages']['canonical_review']['artifact']; channel=package['stages']['linkedin_review']['artifact']
                    proof=json.loads(service.artifacts.get(review['_review_evidence']))
                    source_review=json.loads(service.artifacts.get(brief['_source_review'])); coverage=json.loads(service.artifacts.get(review['_source_coverage']))
                    self.assertIsNone(source_review['draft_ref']); self.assertIsNotNone(coverage['draft_ref'])
                    self.assertNotEqual(source_review['review_session'],coverage['review_session'])
                    self.assertNotEqual(coverage['review_session'],package['stages']['canonical_draft']['artifact']['session_id'])
                    for ref in (brief['outputs'][1],brief['_source_verification'],brief['_source_review'],source_review['tool_evidence_ref'],review['_source_coverage'],coverage['review_result_ref'],review['_review_evidence'],proof['review_output_ref'],channel['_review_evidence']):
                        path=Path(service.artifacts.directory)/ref; original=path.read_bytes()
                        path.unlink(); self.assertFalse(service.verify_terminal('run-1'))
                        path.write_bytes(original); path.chmod(0o400); self.assertTrue(service.verify_terminal('run-1'))
                        path.chmod(0o600); path.write_bytes(b'tampered'); path.chmod(0o400)
                        self.assertFalse(service.verify_terminal('run-1'))
                        path.chmod(0o600); path.write_bytes(original); path.chmod(0o400); self.assertTrue(service.verify_terminal('run-1'))
                    External.available=False
                    unavailable=control.handle('x'*40,req)
                    self.assertEqual(unavailable['reason'],'task freshness unavailable')
                    self.assertIsNotNone(service.graph.get('run-1','assembly'))
                    External.available=True; External.owner='another-owner'
                    stale=control.handle('x'*40,req)
                    self.assertEqual(stale['reason'],'task changed; readmission required')
                    self.assertIsNone(service.graph.get('run-1','assembly'))
                    self.assertIsNone(control.handle('x'*40,{'action':'human_review_status','run':'run-1'})['decision'])
                finally: service.lifecycle.close(); service.close(); engine.shutdown(); engine_thread.join(); engine.server_close()
