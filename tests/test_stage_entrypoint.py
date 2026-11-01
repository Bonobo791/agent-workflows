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
        self._run_marketing(with_asset=False)
    def test_marketing_all_channels_with_synthetic_verified_asset(self):
        self._run_marketing(with_asset=True)
    def test_marketing_snapshot_policy_change_blocks_review_until_restored(self):
        self._run_marketing(with_asset=False,with_snapshots=True)
    def test_review_package_includes_verified_blocked_media_findings(self):
        self._run_marketing(with_asset=True,asset_blocked=True)
    def _run_marketing(self,with_asset,asset_blocked=False,with_snapshots=False):
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
            containers={}; specs=[]; canonical_reviews=0; source_reviews=0; resumed=[]; bad_asset_brief=False
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
                                data=json.dumps({'register_ref':refs[0],'verifier_id':'synthetic-independent-verifier','checks':[{'claim_id':'claim-1','source_id':'source-1','evidence_id':'evidence-1','supported':True,'primary':True}],'draft_ref':refs[1] if payload.get('draft') is not None else None,'omitted_claims':['unregistered material claim'] if Engine.source_reviews==2 else [],'coverage_complete':Engine.source_reviews!=2}).encode()
                                Path(item['mounts']['/output'],'verification.json').write_bytes(data)
                            elif stage=='asset_brief':
                                assert 'Select an exact channel, not a generic label' in msg['params']['input'][0]['text']
                                data=json.dumps({'content_ref':refs[0],'assets':[{'channel':'instagram','kind':'png','width':1080,'height':1080,'duration_seconds':None,'approved_text':['invented text' if Engine.bad_asset_brief else payload['task']],'claim_ids':['claim-1'],'media':[],'captions':'','alt_text':'Synthetic graphic'}]}).encode()
                                Path(item['mounts']['/output'],'result.txt').write_bytes(data)
                            else:
                                data=(('# Fixture\n\n' if stage=='canonical_draft' else '')+'synthetic '+stage+' '+cid[-4:]).encode(); Path(item['mounts']['/output'],'result.txt').write_bytes(data)
                            outputs=[hashlib.sha256(data).hexdigest()]
                            if stage=='brief':
                                source_data=json.dumps(REGISTER).encode(); Path(item['mounts']['/output'],'sources.json').write_bytes(source_data); outputs.append(hashlib.sha256(source_data).hexdigest())
                            status='passed'; findings=[]
                            if stage.endswith('_draft') and stage!='canonical_draft':
                                assert 'ready for independent review' in msg['params']['input'][0]['text']
                            if stage=='canonical_draft' and Engine.resumed:
                                assert 'source_review_judgment' in payload['artifacts']
                                assert 'unregistered material claim' in payload['artifacts']['source_review_judgment']
                            if stage=='canonical_review':
                                assert 'Independently review the supplied canonical draft' in msg['params']['input'][0]['text']
                                assert 'do not require rendered media at this stage' in msg['params']['input'][0]['text']
                                assert 'synthetic brief' in payload['artifacts']['brief_and_sources']
                                assert len(refs)==(6 if with_snapshots else 5) and 'source_register' in payload['artifacts'] and 'source_verification' in payload['artifacts'] and 'page_rules' in payload['artifacts']
                                assert ('source_snapshots' in payload['artifacts'])==with_snapshots
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
            roles=['brief','canonical_draft','canonical_review','source_review','asset_brief','asset_review']+[c+s for c in ('linkedin','x','facebook','instagram','reddit') for s in ('_draft','_review')]
            with patch('runner.plan_mcp.HTTPSMCP',return_value=PlanHTTP()),patch('workflows.engineering.github.HTTPSGitHub',return_value=GithubHTTP()):
                service,control=build_control(root,'x'*40,docker_config=docker,task_config=task,plan_token='synthetic-plan',github_token='synthetic-github',role_config={'skill_roots':[str(skillroot)],'required_skills':{role:['role'] for role in roles}},lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
                try:
                    service.page_rules={'guide':{'headings':[{'level':1,'text':'Fixture'}]}}
                    service.source_tool_authority=lambda events,register,draft: all(item.get('type')=='mcpToolCall' and item.get('server')=='source-evidence' and item.get('tool')=='fetch_primary' and item.get('status')=='completed' and item.get('arguments',{}).get('url')=='https://example.invalid/evidence' and item.get('result',{}).get('content',[{}])[0].get('text')=='fixture primary source' for item in events['items']); service.docker_supervisor.credential_delivery=Delivery(); service.docker_supervisor.provider_ready=lambda:True
                    if with_snapshots:
                        from runner.source_snapshots import SourceSnapshots
                        from tests.test_source_snapshots import Fetcher
                        service.source_snapshots=SourceSnapshots(service,Fetcher())
                    class RedditAuthority:
                        def check(self,run,draft_ref):
                            import time
                            now=time.time(); rules=service.artifacts.put(b'synthetic community rules')
                            return service.artifacts.put(json.dumps({'draft_ref':draft_ref,'community':'example','rules_ref':rules,'checked_at':now,'expires_at':now+300,'relevant':True,'product_mentioned':False,'affiliation_disclosed':False}).encode())
                        def verify(self,run,draft_ref,context_ref): return True
                    service.reddit_context_authority=RedditAuthority()
                    if with_asset:
                        import io, time, zipfile
                        from tests.test_assets import png
                        from runner.graph import GraphStore, marketing_definition
                        from runner.provenance import Provenance
                        render=Path(root)/'render'; render.mkdir(mode=0o700)
                        image=png(1080,1080); (render/'asset.png').write_bytes(image); (render/'preview.png').write_bytes(image)
                        report=json.dumps({'ok':True,'lines':['✓ fixture: same picture']}).encode(); (render/'look.json').write_bytes(report)
                        project=io.BytesIO()
                        with zipfile.ZipFile(project,'w') as archive: archive.writestr('film.html','<html>synthetic project</html>')
                        project_ref=service.artifacts.put(project.getvalue()); terminal=service.artifacts.put(b'synthetic terminal evidence')
                        class Platform:
                            def check(self,run,brief_ref,brief):
                                now=time.time(); source=service.artifacts.put(b'synthetic platform requirements')
                                assets=[{**{key:item[key] for key in ('channel','kind','width','height','duration_seconds')},'source_ref':source,'supported':True} for item in brief['assets']]
                                return service.artifacts.put(json.dumps({'brief_ref':brief_ref,'checked_at':now,'expires_at':now+300,'assets':assets}).encode())
                            def verify(self,*args): return True
                        class Backend:
                            timeout_seconds=10
                            def ready(self): return True
                            def cancel(self,*args): pass
                            def execute(self,**kwargs):
                                image_ref=hashlib.sha256(image).hexdigest()
                                return {'attempt_id':kwargs['attempt_id'],'status':'exited','root':str(render),'project_ref':project_ref,'terminal_ref':terminal,'outputs':[{'path':'asset.png','sha256':image_ref,'previews':[{'path':'preview.png','sha256':image_ref}],'report':{'path':'look.json','sha256':hashlib.sha256(report).hexdigest()}}]}
                            def verify_terminal(self,run,attempt,brief_ref,result): return run=='run-1' and result['attempt_id']==attempt and result['project_ref']==project_ref and result['terminal_ref']==terminal
                        class AssetReviewer:
                            timeout_seconds=10
                            def ready(self): return True
                            def cancel(self,*args): pass
                            def review(self,*,run,attempt_id,claim,subject,inputs,asset_refs,skills,skill_contents,model):
                                graph=GraphStore(root+'/state.db'); graph.register(marketing_definition())
                                try:
                                    provenance=Provenance(graph); session='synthetic-asset-review'
                                    checks={'content_fidelity':True,'brand_fidelity':True,'rights':True,'platform_suitability':True,'readability':True,'captions':True,'safe_zones':True,'audio':True}
                                    if asset_blocked: checks['safe_zones']=False
                                    report_ref=service.artifacts.put(json.dumps({'assets':[{'artifact_ref':ref,**checks} for ref in asset_refs]}).encode())
                                    finding={'id':'safe-zone','severity':'blocking','description':'Title too close to edge.','location':'image','evidence':asset_refs[0],'remedy':'Increase margin.','resolution':'open'}
                                    result={'stage':'asset_review','status':'blocked' if asset_blocked else 'passed','input_versions':inputs,'outputs':[report_ref],'findings':[finding] if asset_blocked else []}
                                    if asset_blocked: result['reason']='Safe area unverified.'
                                    response=service.artifacts.put(json.dumps(result).encode())
                                    access=service.artifacts.put(b'synthetic media access evidence')
                                    provenance.bind(run,'asset_review',session,skills,claim,fresh=True,subject=subject); provenance.mark_completed(run,'asset_review',session,claim,response)
                                    return {'attempt_id':attempt_id,'session_id':session,'response_ref':response,'access_ref':access}
                                finally: graph.close()
                            def verify_access(self,run,session,subject,refs,access):
                                return run=='run-1' and session=='synthetic-asset-review' and service.artifacts.get(access)==b'synthetic media access evidence' and all(service.artifacts.get(ref)==image for ref in refs)
                        service.openfilm_platform_authority=Platform(); service.openfilm_backend=Backend(); service.openfilm_reviewer=AssetReviewer()
                    service.approve('controller','docker_config',service.docker_supervisor.config.policy_hash,'operator')
                    control.handle('x'*40,{'action':'submit_configured'}); q=control.handle('x'*40,{'action':'manual_claim'})['claim']; g=control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
                    req={'action':'dispatch_stage','run':'run-1','queue_claim':q['token'],'graph_claim':g}
                    if with_asset:
                        self.assertEqual(control.handle('x'*40,req)['stage'],'intake')
                        service.graph.db.execute("INSERT INTO quota_pauses VALUES('default','run-1','brief')")
                        self.assertEqual(control.handle('x'*40,req),{'status':'rate_limited','reason':'subscription quota paused; explicit resume required'})
                        control.handle('x'*40,{'action':'resume_quota','scope':'default'})
                        skill=skillroot/'role'/'SKILL.md'; skill.unlink()
                        self.assertEqual(control.handle('x'*40,req),{'stage':'brief','status':'blocked','reason':'required skills unavailable'})
                        skill.write_text('Synthetic role')
                        service.docker_supervisor.provider_ready=lambda:False
                        self.assertEqual(control.handle('x'*40,req),{'stage':'brief','status':'blocked','reason':'credential delivery or runtime approval unavailable'})
                        self.assertEqual(service.graph.db.execute("SELECT COUNT(*) FROM graph_attempts WHERE run='run-1' AND stage='brief'").fetchone()[0],0)
                        service.docker_supervisor.provider_ready=lambda:True
                    for _ in range(30):
                        result=control.handle('x'*40,req)
                        if result.get('status')=='awaiting_approval': control.handle('x'*40,{'action':'approve','run':'run-1','approval':'freeze','version':result['version']})
                        if result.get('stage')=='assembly' and result.get('status')=='awaiting_review': break
                    self.assertEqual(result['status'],'awaiting_review'); self.assertEqual(Engine.canonical_reviews,2); self.assertEqual(Engine.source_reviews,3); self.assertEqual(len(Engine.resumed),1)
                    self.assertEqual(service.graph.get('run-1','asset_production')['status'],'passed' if with_asset and not asset_blocked else 'blocked')
                    self.assertEqual(service.graph.get('run-1','reddit_review')['status'],'passed')
                    package=json.loads(service.artifacts.get(service.graph.get('run-1','assembly')['version']))
                    if with_asset and not asset_blocked:
                        for channel in ('linkedin','x','facebook','instagram','reddit'): self.assertEqual(package['stages'][channel+'_review']['status'],'passed')
                    if asset_blocked:
                        self.assertEqual(package['stages']['instagram_review']['status'],'blocked')
                        self.assertEqual(package['stages']['asset_production']['asset_review']['reason'],'Safe area unverified.')
                        self.assertEqual(package['stages']['asset_production']['asset_review']['findings'][0]['description'],'Title too close to edge.')
                    self.assertIn('synthetic canonical_draft',package['stages']['canonical_draft']['contents'][0])
                    self.assertIn('synthetic brief',package['stages']['brief']['contents'][0])
                    self.assertEqual(package['stages']['brief']['source_verification']['verifier_id'],'synthetic-independent-verifier')
                    self.assertIn('claim-1',package['stages']['brief']['contents'][1])
                    self.assertIn('synthetic linkedin_draft',package['stages']['linkedin_draft']['contents'][0])
                    self.assertTrue(service.verify_terminal('run-1'))
                    if with_snapshots:
                        from runner.control import ControlError
                        service.source_snapshots.fetcher.max_bytes=512
                        self.assertFalse(service.verify_terminal('run-1'))
                        decision={'action':'human_review_decision','run':'run-1','queue_claim':q['token'],'graph_claim':g,'version':service.graph.get('run-1','assembly')['version'],'decision':'approved'}
                        with self.assertRaises(ControlError): control.handle('x'*40,decision)
                        self.assertEqual(service.graph.db.execute("SELECT count(*) FROM human_review_decisions WHERE run='run-1'").fetchone()[0],0)
                        service.source_snapshots.fetcher.max_bytes=1024
                        self.assertTrue(service.verify_terminal('run-1'))
                    version=service.graph.get('run-1','assembly')['version']
                    from runner.control import ControlError
                    decision={'action':'human_review_decision','run':'run-1','queue_claim':q['token'],'graph_claim':g,'version':'0'*64,'decision':'approved'}
                    with self.assertRaises(ControlError): control.handle('x'*40,decision)
                    decision['version']=version
                    service.graph.db.execute("CREATE TRIGGER reject_human_audit BEFORE INSERT ON human_review_audit BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END")
                    with self.assertRaises(ControlError): control.handle('x'*40,decision)
                    self.assertIsNone(control.handle('x'*40,{'action':'human_review_status','run':'run-1'})['decision'])
                    service.graph.db.execute('DROP TRIGGER reject_human_audit')
                    self.assertEqual(control.handle('x'*40,decision)['decision'],'approved')
                    self.assertEqual(control.handle('x'*40,{'action':'human_review_status','run':'run-1'})['decision']['version'],version)
                    with self.assertRaises(ControlError): control.handle('x'*40,decision)
                    brief=package['stages']['brief']['artifact']; review=package['stages']['canonical_review']['artifact']; channel=package['stages']['linkedin_review']['artifact']
                    proof=json.loads(service.artifacts.get(review['_review_evidence']))
                    source_review=json.loads(service.artifacts.get(brief['_source_review'])); coverage=json.loads(service.artifacts.get(review['_source_coverage']))
                    self.assertIsNone(source_review['draft_ref']); self.assertIsNotNone(coverage['draft_ref'])
                    self.assertNotEqual(source_review['review_session'],coverage['review_session'])
                    self.assertNotEqual(coverage['review_session'],package['stages']['canonical_draft']['artifact']['session_id'])
                    refs=[brief['outputs'][1],brief['_source_verification'],brief['_source_review'],source_review['tool_evidence_ref'],review['_source_coverage'],coverage['review_result_ref'],review['_review_evidence'],proof['review_output_ref'],channel['_review_evidence']]
                    if with_asset:
                        asset=json.loads(service.artifacts.get(service.graph.get('run-1','asset_production')['version']))
                        manifest=json.loads(service.artifacts.get(asset['manifest_ref']))
                        self.assertEqual(service.artifacts.get(manifest['assets'][0]['artifact_ref']),image)
                        refs.extend([asset['manifest_ref'],asset['access_ref'],asset['report_ref'],asset['response_ref'],manifest['platform_ref'],manifest['assets'][0]['artifact_ref']])
                    for ref in refs:
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
                    External.available=True
                    service.graph.invalidate('run-1','asset_brief',service.graph.prepare('run-1','asset_brief',g),g)
                    Engine.bad_asset_brief=True
                    invalid=control.handle('x'*40,req)
                    self.assertEqual((invalid['stage'],invalid['status']),('asset_brief','blocked'))
                    External.owner='another-owner'
                    stale=control.handle('x'*40,req)
                    self.assertEqual(stale['reason'],'task changed; readmission required')
                    self.assertIsNone(service.graph.get('run-1','assembly'))
                    self.assertIsNone(control.handle('x'*40,{'action':'human_review_status','run':'run-1'})['decision'])
                finally: service.lifecycle.close(); service.close(); engine.shutdown(); engine_thread.join(); engine.server_close()
                import sqlite3
                from contextlib import closing
                from runner.artifacts import ArtifactStore
                with closing(sqlite3.connect(root+'/state.db')) as reopened:
                    self.assertEqual(reopened.execute("SELECT decision FROM human_review_decisions WHERE run='run-1' AND version=?",(version,)).fetchone(),('approved',))
                    self.assertEqual(reopened.execute("SELECT status FROM human_review_audit WHERE run='run-1' AND version=?",(version,)).fetchone(),('approved',))
                    self.assertTrue(reopened.execute("SELECT 1 FROM execution_turn_audit WHERE run='run-1' AND role='canonical_review'").fetchone())
                    tools=reopened.execute("SELECT role,tool_kind,status,input_present,output_present FROM tool_audit WHERE run='run-1' ORDER BY id").fetchall()
                    self.assertTrue(tools)
                    self.assertTrue(all(row==('source_review','mcpToolCall','completed',1,1) for row in tools))
                    self.assertTrue(reopened.execute("SELECT 1 FROM graph_audit WHERE run='run-1' AND status='invalidated'").fetchone())
                restored=json.loads(ArtifactStore(root+'/artifacts').get(version))
                self.assertEqual(restored['stages']['brief']['artifact']['_source_review'],brief['_source_review'])
