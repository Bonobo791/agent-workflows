import hashlib
import http.client
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from runner.worker import build_control
from runner.local_scope import LocalScopes
from runner.local_supervisor import LocalSupervisor
from runner.manual_lifecycle import ManualLifecycle
from runner.process_transport import ProcessTransport
from tests.source_fixture import verifier as fixture_verifier
from tests.test_local_scope import Backend as ScopeBackend

FIXTURE = Path(__file__).resolve().parent/'fixtures'/'fake_marketing_codex.py'

class StageEntrypointTests(unittest.TestCase):
    def test_marketing_correction_fanout_and_human_gate_over_fake_protocols(self):
        self._run_marketing(with_asset=False)
    def test_marketing_all_channels_with_synthetic_verified_asset(self):
        self._run_marketing(with_asset=True)
    def test_marketing_snapshot_policy_change_blocks_review_until_restored(self):
        self._run_marketing(with_asset=False,with_snapshots=True)
    def test_review_package_includes_verified_blocked_media_findings(self):
        self._run_marketing(with_asset=True,asset_blocked=True)
    def test_platform_block_evidence_must_remain_verified(self):
        self._run_marketing(with_asset=True,platform_blocked=True)
    def test_reddit_irrelevant_context_is_unsuitable(self):
        self._run_marketing(with_asset=True,reddit_context='irrelevant')
    def test_reddit_product_without_disclosure_stays_blocked(self):
        self._run_marketing(with_asset=True,reddit_context='product')
    def test_reddit_missing_context_stays_blocked(self):
        self._run_marketing(with_asset=True,reddit_context='missing')
    def test_reddit_expired_context_stays_blocked(self):
        self._run_marketing(with_asset=True,reddit_context='expired')
    def test_approved_package_completes_with_current_platform_block(self):
        self._run_marketing(with_asset=True,platform_blocked=True,complete_job=True)
    def _run_marketing(self,with_asset,asset_blocked=False,with_snapshots=False,platform_blocked=False,complete_job=False,reddit_context='current'):
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
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700); skillroot=Path(root)/'skills'; (skillroot/'role').mkdir(parents=True); (skillroot/'role'/'SKILL.md').write_text('Synthetic role'); (skillroot/'plan-seo-geo-growth').mkdir(); (skillroot/'plan-seo-geo-growth'/'SKILL.md').write_text('Synthetic SEO')
            state_path=Path(root)/'codex-state.json'
            state_path.write_text(json.dumps({'counter':0,'threads':{},'canonical_reviews':0,'source_reviews':0,'resumed':[],'bad_asset_brief':False,'errors':[]}))
            def read_state(): return json.loads(state_path.read_text())
            def write_state(**updates): state_path.write_text(json.dumps({**read_state(),**updates},sort_keys=True))
            outputs=Path(root)/'outputs'; outputs.mkdir(mode=0o700)
            binary=Path(root)/'codex'; binary.write_text('synthetic'); binary.chmod(0o700)
            scope_backend=ScopeBackend(); handles=[]
            class Handle:
                def __init__(self,run,workspace):
                    self.run=run; self.output_dir=tempfile.mkdtemp(dir=outputs); self.output_path_prefix=self.output_dir
                    scopes=LocalScopes(str(Path(root)/'state.db'),scope_backend)
                    try:
                        self.unit=scopes.reserve(run); scope_backend.unit=self.unit; scope_backend.state='active'; scopes.bind(run,self.unit)
                    finally: scopes.close()
                    self.process=subprocess.Popen([sys.executable,str(FIXTURE)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,env={**os.environ,'OUTPUT':self.output_dir,'FAKE_ROOT':root,'FAKE_STATE':str(state_path),'FAKE_WORKSPACE':workspace,'FAKE_WITH_SNAPSHOTS':'1' if with_snapshots else '0'})
                    handles.append(self)
                def assert_active(self): return True
                def open_transport(self,deadline,cancelled): return ProcessTransport(self.process,deadline,cancelled)
                def verify_config(self,request): return True
                def finish(self):
                    self.process.stdin.close(); self.process.wait(timeout=10); self.process.stdout.close()
                    scopes=LocalScopes(str(Path(root)/'state.db'),scope_backend)
                    try: return scopes.stop(self.run,self.unit)
                    finally: scopes.close()
                def cancel(self): return self.finish()
            factory=lambda service:LocalSupervisor(str(Path(root)/'state.db'),str(binary),{'HOME':root},scope_backend,lambda run:True,lambda run,role:str(Path(root)/'workspaces'/role),lambda:True,{'mcp_servers':[],'plugins':[]},policy_hash='a'*64)
            task={'run':'run-1','workflow':'marketing','project':'DEMO','number':1,'task_format':'guide','ready_columns':[10],'completed_columns':[20],'blocker_direction':'inward','plan_endpoint':'https://plan.example.invalid/mcp','github_owner':'owner','github_repo':'repo','github_branch':'task-1'}
            roles=['brief','canonical_draft','canonical_review','source_review','asset_brief','asset_review']+[c+s for c in ('linkedin','x','facebook','instagram','reddit') for s in ('_draft','_review')]
            with patch('runner.plan_mcp.HTTPSMCP',return_value=PlanHTTP()),patch('workflows.engineering.github.HTTPSGitHub',return_value=GithubHTTP()),patch('runner.local_supervisor.start_local_process',side_effect=lambda db,run,workspace,*args,**kwargs:Handle(run,workspace)):
                service,control=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,task_config=task,plan_token='synthetic-plan',github_token='synthetic-github',role_config={'skill_roots':[str(skillroot)],'required_skills':{role:['role'] for role in roles}},lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
                try:
                    service.page_rules={'guide':{'headings':[{'level':1,'text':'Fixture'}]}}
                    service.source_tool_authority=lambda events,register,draft: all(item.get('type')=='mcpToolCall' and item.get('server')=='source-evidence' and item.get('tool')=='fetch_primary' and item.get('status')=='completed' and item.get('arguments',{}).get('url')=='https://example.invalid/evidence' and item.get('result',{}).get('content',[{}])[0].get('text')=='fixture primary source' for item in events['items']); service.model_supervisor.preflight=lambda:True
                    if with_snapshots:
                        from runner.source_snapshots import SourceSnapshots
                        from tests.test_source_snapshots import Fetcher
                        service.source_snapshots=SourceSnapshots(service,Fetcher())
                    class RedditAuthority:
                        def check(self,run,draft_ref):
                            import time
                            now=time.time(); rules=service.artifacts.put(b'synthetic community rules')
                            expires=now-1 if reddit_context=='expired' else now+300
                            return service.artifacts.put(json.dumps({'draft_ref':draft_ref,'community':'example','rules_ref':rules,'checked_at':now,'expires_at':expires,'relevant':reddit_context!='irrelevant','product_mentioned':reddit_context=='product','affiliation_disclosed':False}).encode())
                        def verify(self,run,draft_ref,context_ref): return True
                    service.reddit_context_authority=None if reddit_context=='missing' else RedditAuthority()
                    if with_asset:
                        import io, time, zipfile
                        from tests.test_assets import png
                        from runner.graph import GraphStore, marketing_definition
                        from runner.provenance import Provenance
                        render=Path(root)/'render'; render.mkdir(mode=0o700)
                        image=png(1080,1080); (render/'asset.png').write_bytes(image); (render/'preview.png').write_bytes(image)
                        report=json.dumps({'ok':True,'lines':['✓ fixture: same picture']}).encode(); (render/'look.json').write_bytes(report)
                        project=io.BytesIO()
                        from workflows.marketing.gates import CHANNELS
                        with zipfile.ZipFile(project,'w') as archive:
                            for index in range(len(CHANNELS)): archive.writestr(f'projects/{index}/film.html','<html>synthetic project</html>')
                        project_ref=service.artifacts.put(project.getvalue()); terminal=service.artifacts.put(b'synthetic terminal evidence')
                        class Platform:
                            valid=True
                            def check(self,run,brief_ref,brief):
                                if platform_blocked:
                                    self.proof=service.artifacts.put(json.dumps({'brief_ref':brief_ref,'reason':'Exact image requirements unavailable.','findings':[]}).encode())
                                    return {'blocked_ref':self.proof}
                                now=time.time(); source=service.artifacts.put(b'synthetic platform requirements')
                                assets=[{**{key:item[key] for key in ('channel','kind','width','height','duration_seconds')},'source_ref':source,'supported':True} for item in brief['assets']]
                                return service.artifacts.put(json.dumps({'brief_ref':brief_ref,'checked_at':now,'expires_at':now+300,'assets':assets}).encode())
                            def verify(self,*args): return True
                            def verify_blocked(self,run,brief_ref,brief,proof): return self.valid and proof==self.proof and json.loads(service.artifacts.get(proof))['brief_ref']==brief_ref
                        class Backend:
                            timeout_seconds=10
                            def ready(self): return True
                            def cancel(self,*args): pass
                            def execute(self,**kwargs):
                                outputs=[]
                                for index,_ in enumerate(kwargs['brief']['assets']):
                                    look=json.dumps({'ok':True,'lines':['✓ sampled text layout inspected','✓ fixture: same picture']}).encode()
                                    (render/f'asset-{index}.png').write_bytes(image); (render/f'preview-{index}.png').write_bytes(image); (render/f'look-{index}.json').write_bytes(look)
                                    outputs.append({'path':f'asset-{index}.png','sha256':hashlib.sha256(image).hexdigest(),'previews':[{'path':f'preview-{index}.png','sha256':hashlib.sha256(image).hexdigest()}],'report':{'path':f'look-{index}.json','sha256':hashlib.sha256(look).hexdigest()}})
                                return {'attempt_id':kwargs['attempt_id'],'status':'exited','root':str(render),'project_ref':project_ref,'terminal_ref':terminal,'outputs':outputs}
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
                        service.model_supervisor.preflight=lambda:False
                        self.assertEqual(control.handle('x'*40,req),{'stage':'brief','status':'blocked','reason':'credential delivery or runtime approval unavailable'})
                        self.assertEqual(service.graph.db.execute("SELECT COUNT(*) FROM graph_attempts WHERE run='run-1' AND stage='brief'").fetchone()[0],0)
                        service.model_supervisor.preflight=lambda:True
                    for _ in range(30):
                        result=control.handle('x'*40,req)
                        if result.get('status')=='awaiting_approval': control.handle('x'*40,{'action':'approve','run':'run-1','approval':'freeze','version':result['version']})
                        if platform_blocked and result.get('stage')=='reddit_review':
                            from runner.control import ControlError
                            service.openfilm_platform_authority.valid=False
                            with self.assertRaises(ControlError): control.handle('x'*40,req)
                            service.openfilm_platform_authority.valid=True
                        if result.get('stage')=='assembly' and result.get('status')=='awaiting_review': break
                    self.assertEqual(read_state()['errors'],[]); self.assertEqual(result['status'],'awaiting_review'); self.assertEqual(read_state()['canonical_reviews'],2); self.assertEqual(read_state()['source_reviews'],3); self.assertEqual(len(read_state()['resumed']),1)
                    self.assertEqual(service.graph.get('run-1','asset_production')['status'],'passed' if with_asset and not asset_blocked and not platform_blocked else 'blocked')
                    asset_passed=with_asset and not asset_blocked and not platform_blocked
                    self.assertEqual(service.graph.get('run-1','reddit_review')['status'],{'current':'passed','irrelevant':'unsuitable'}.get(reddit_context,'blocked') if asset_passed else 'blocked')
                    package=json.loads(service.artifacts.get(service.graph.get('run-1','assembly')['version']))
                    if with_asset and not asset_blocked and not platform_blocked:
                        for channel in ('linkedin','x','facebook','instagram','reddit'): self.assertEqual(package['stages'][channel+'_review']['status'],'passed' if channel!='reddit' or reddit_context=='current' else package['stages'][channel+'_review']['status'])
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
                    if with_asset and not asset_blocked and not platform_blocked and reddit_context=='current':
                        from runner.stage_dispatch import StageError
                        service.reddit_context_authority.verify=lambda *_:False
                        with self.assertRaises(StageError): service.stage_dispatcher._verify_recorded_review('run-1','reddit_review')
                        service.reddit_context_authority.verify=lambda *_:True
                    if platform_blocked:
                        from runner.control import ControlError
                        service.openfilm_platform_authority.valid=False
                        self.assertFalse(service.verify_terminal('run-1'))
                        decision={'action':'human_review_decision','run':'run-1','queue_claim':q['token'],'graph_claim':g,'version':service.graph.get('run-1','assembly')['version'],'decision':'approved'}
                        with self.assertRaises(ControlError): control.handle('x'*40,decision)
                        self.assertEqual(service.graph.db.execute('SELECT count(*) FROM human_review_decisions').fetchone()[0],0)
                        service.openfilm_platform_authority.valid=True
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
                    brief=package['stages']['brief']['artifact']
                    if complete_job:
                        service.finish_job('run-1',q['token'],g)
                        self.assertEqual(service.queue.get('run-1')['status'],'completed')
                        service.request_lifecycle_stop('run-1')
                        self.assertEqual(service.lifecycle.confirm_operator_stop('run-1','inc','terminated_and_fenced')['status'],'stopped')
                        self.assertEqual(service.queue.get('run-1')['status'],'completed')
                    else:
                        with self.assertRaises(ControlError): control.handle('x'*40,decision)
                        brief=package['stages']['brief']['artifact']; review=package['stages']['canonical_review']['artifact']; channel=package['stages']['linkedin_review']['artifact']
                        proof=json.loads(service.artifacts.get(review['_review_evidence']))
                        source_review=json.loads(service.artifacts.get(brief['_source_review'])); coverage=json.loads(service.artifacts.get(review['_source_coverage']))
                        self.assertIsNone(source_review['draft_ref']); self.assertIsNotNone(coverage['draft_ref'])
                        self.assertNotEqual(source_review['review_session'],coverage['review_session'])
                        self.assertNotEqual(coverage['review_session'],package['stages']['canonical_draft']['artifact']['session_id'])
                        refs=[brief['outputs'][1],brief['_source_verification'],brief['_source_review'],source_review['tool_evidence_ref'],review['_source_coverage'],coverage['review_result_ref'],review['_review_evidence'],proof['review_output_ref'],channel['_review_evidence']]
                        if with_asset and not platform_blocked:
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
                        write_state(bad_asset_brief=True)
                        invalid=control.handle('x'*40,req)
                        self.assertEqual((invalid['stage'],invalid['status']),('asset_brief','blocked'))
                        External.owner='another-owner'
                        stale=control.handle('x'*40,req)
                        self.assertEqual(stale['reason'],'task changed; readmission required')
                        self.assertIsNone(service.graph.get('run-1','assembly'))
                        self.assertIsNone(control.handle('x'*40,{'action':'human_review_status','run':'run-1'})['decision'])
                finally:
                    service.lifecycle.close(); service.close()
                    for handle in handles:
                        if handle.process.poll() is None: handle.process.kill(); handle.process.wait()
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
                    if complete_job:
                        self.assertEqual(reopened.execute("SELECT status FROM jobs WHERE run='run-1'").fetchone(),('completed',))
                        self.assertEqual(reopened.execute("SELECT status FROM graph_runs WHERE id='run-1'").fetchone(),('completed',))
                        self.assertFalse(reopened.execute("SELECT 1 FROM graph_claims WHERE run='run-1'").fetchone())
                        self.assertTrue(reopened.execute("SELECT 1 FROM claim_audit WHERE run='run-1' AND status='released'").fetchone())
                    else:self.assertTrue(reopened.execute("SELECT 1 FROM graph_audit WHERE run='run-1' AND status='invalidated'").fetchone())
                restored=json.loads(ArtifactStore(root+'/artifacts').get(version))
                self.assertEqual(restored['stages']['brief']['artifact']['_source_review'],brief['_source_review'])
