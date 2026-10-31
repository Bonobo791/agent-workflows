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

class EngineeringEntrypointTests(unittest.TestCase):
    def test_engineering_admission_implementation_and_qa_to_approval(self):
        class External(http.server.BaseHTTPRequestHandler):
            version='v1'
            def log_message(self,*args): pass
            def do_GET(self): self.reply([])
            def do_POST(self):
                message=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if message['method']=='notifications/initialized': self.send_response(202); self.end_headers(); return
                if message['method']=='initialize': result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}}}
                else:
                    name=message['params']['name']
                    if name=='get_issue_by_number': data={'id':1,'identifier':'DEMO-1','title':'Synthetic','description':'Example','columnId':10,'archivedAt':None,'assigneeUserId':None,'delegateUserId':None,'dueDate':None,'startDate':None,'subtasks':[],'links':[],'development':[],'updatedAt':External.version}
                    elif name=='list_issue_activity': data={'items':[],'nextCursor':None}
                    else: data=[]
                    result={'structuredContent':{'ok':True,'data':data}}
                self.reply({'jsonrpc':'2.0','id':message['id'],'result':result})
            def reply(self,data):
                payload=json.dumps(data).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
        class Engine(socketserver.StreamRequestHandler):
            containers={}; specs=[]; canonical_reviews=0; resumed=[]
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
                    Engine.containers[cid]={'state':'created','label':spec['Labels']['agent-workflows.run'],'mounts':mounts,'command':spec['Entrypoint']+spec['Cmd'] if 'Entrypoint' in spec else None,'code':0,'stdout':b'','stderr':b''}; reply(201,{'Id':cid}); return
                cid=path.split('/')[3]; item=Engine.containers[cid]
                if path.endswith('/json'): reply(200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run':item['label']}},'State':{'Running':item['state']=='running','Status':item['state'],'ExitCode':item['code']}})
                elif path.endswith('/start'):
                    item['state']='running'
                    if item['command'] is not None:
                        import subprocess
                        completed=subprocess.run(item['command'],cwd=item['mounts']['/workspace'],env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent'},capture_output=True,timeout=5,check=False)
                        item['code']=completed.returncode; item['stdout']=completed.stdout; item['stderr']=completed.stderr; item['state']='exited'
                    reply(204)
                elif '/stop?' in path: item['state']='exited'; reply(204)
                elif '/wait?' in path: reply(200,{'StatusCode':item['code']})
                elif '/logs?' in path:
                    payload=b''.join(struct.pack('>BxxxI',channel,len(data))+data for channel,data in ((1,item['stdout']),(2,item['stderr'])) if data)
                    self.wfile.write(('HTTP/1.1 200 OK\r\nContent-Length: '+str(len(payload))+'\r\nContent-Type: application/vnd.docker.raw-stream\r\n\r\n').encode()+payload); self.wfile.flush()
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
                            data=(json.dumps({'commands':[{'command':'synthetic-check','exit_code':0,'output':'fixture passed'}]}) if stage=='test' else 'synthetic '+stage+' '+cid[-4:]).encode(); Path(item['mounts']['/output'],'result.txt').write_bytes(data)
                            status='passed'; findings=[]
                            if stage=='implement':
                                import subprocess
                                workspace=item['mounts']['/workspace']
                                Path(workspace,'new.txt').write_text('corrected fixture v2' if len(Engine.resumed)>1 else 'corrected fixture' if Engine.resumed else 'implemented fixture')
                                subprocess.run(['git','add','new.txt'],cwd=workspace,check=True,capture_output=True)
                                subprocess.run(['git','commit','-m','implementation'],cwd=workspace,check=True,capture_output=True)
                            if stage=='qa':
                                Engine.canonical_reviews+=1
                                if Engine.canonical_reviews in (1,3):
                                    status='blocked'; findings=[{'id':'f1','severity':'blocking','description':'Fix fixture','location':'new.txt','evidence':refs[1],'remedy':'Correct content','resolution':'open'}]
                            result={'stage':stage,'status':status,'input_versions':refs,'outputs':[hashlib.sha256(data).hexdigest()],'findings':findings}
                            if status=='blocked': result['reason']='correction required'
                            emit({'id':ident,'result':{'turn':{'id':'turn-'+cid[-4:]}}})
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
            import subprocess
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            workspace=Path(root)/'workspaces'/'run-1'/'repo'; workspace.mkdir(parents=True,mode=0o700)
            (Path(root)/'workspaces').chmod(0o700); workspace.parent.chmod(0o700)
            def git(*args): return subprocess.run(['git',*args],cwd=workspace,check=True,capture_output=True,text=True).stdout.strip()
            git('init','-b','main'); (workspace/'.git').chmod(0o700); git('config','user.name','Fixture'); git('config','user.email','fixture@example.invalid')
            (workspace/'base.txt').write_text('base'); git('add','base.txt'); git('commit','-m','base'); base=git('rev-parse','HEAD'); git('checkout','-b','task-1'); git('remote','add','origin','https://github.com/owner/repo.git')
            skillroot=Path(root)/'skills'; (skillroot/'role').mkdir(parents=True); (skillroot/'role'/'SKILL.md').write_text('Synthetic role')
            sock=str(Path(root)/'engine.sock'); engine=EngineServer(sock,Engine); engine_thread=threading.Thread(target=engine.serve_forever); engine_thread.start()
            docker={'socket':sock,'image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            task={'run':'run-1','workflow':'engineering','project':'DEMO','number':1,'ready_columns':[10],'completed_columns':[20],'blocker_direction':'inward','plan_endpoint':'https://plan.example.invalid/mcp','github_owner':'owner','github_repo':'repo','github_branch':'task-1','github_base':'main'}
            roles=['plan','implement','test','qa']
            command={'argv':['/usr/bin/python3','-c','print("fixture test passed")'],'timeout_seconds':10,'output_limit':4096}
            with patch('runner.plan_mcp.HTTPSMCP',return_value=PlanHTTP()),patch('workflows.engineering.github.HTTPSGitHub',return_value=GithubHTTP()):
                service,control=build_control(root,'x'*40,docker_config=docker,command_config=command,task_config=task,plan_token='synthetic-plan',github_token='synthetic-github',role_config={'skill_roots':[str(skillroot)],'required_skills':{role:['role'] for role in roles}},lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
                try:
                    service.docker_supervisor.credential_delivery=Delivery(); service.docker_supervisor.provider_ready=lambda:True
                    service.approve('controller','command_policy',service.command_supervisor.policy_hash,'operator')
                    service.approve('controller','docker_config',service.docker_supervisor.config.policy_hash,'operator')
                    control.handle('x'*40,{'action':'submit_configured'}); q=control.handle('x'*40,{'action':'manual_claim'})['claim']; g=control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
                    binding={'action':'engineering_workspace_binding','run':'run-1','queue_claim':q['token'],'graph_claim':g,'base':base}
                    proposal=control.handle('x'*40,binding); binding.update(action='bind_engineering_workspace',binding_hash=proposal['binding_hash']); control.handle('x'*40,binding)
                    req={'action':'dispatch_stage','run':'run-1','queue_claim':q['token'],'graph_claim':g}
                    seen=[]
                    for _ in range(10):
                        result=control.handle('x'*40,req); seen.append(result.get('stage'))
                        if result.get('status')=='awaiting_approval': break
                    self.assertEqual(seen,['intake','plan','implement','test','qa','implement','test','qa','open_draft'])
                    self.assertEqual(result['status'],'awaiting_approval'); self.assertEqual(Engine.canonical_reviews,2); self.assertEqual(len(Engine.resumed),1)
                    from runner.metrics import snapshot, prometheus_text
                    wait_metric='workflow_stages{workflow="engineering",stage="open_draft",status="awaiting_approval"} 1'
                    self.assertIn(wait_metric,prometheus_text(snapshot(service.graph.db)))
                    original_package=result['version'];old_queue,old_graph=q['token'],g
                    from types import SimpleNamespace
                    from runner.checkpoint import checkpoint
                    from runner.local_scope import LocalScopes
                    from tests.test_local_scope import Backend
                    scopes=LocalScopes(root+'/state.db',Backend());scopes.close()
                    service.graph.db.execute('CREATE TABLE local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
                    service.model_supervisor=SimpleNamespace(db=root+'/state.db')
                    checkpoint(service,'run-1',old_queue,old_graph)
                    self.assertNotIn(wait_metric,prometheus_text(snapshot(service.graph.db)))
                    service.lifecycle.register_checkpoint_replacement('run-1','inc','inc-next')
                    service.lifecycle.close();service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc-next')
                    q=service.lifecycle.claim();g=service.claim('run-1');service.model_supervisor=None
                    req.update(queue_claim=q['token'],graph_claim=g)
                    result=control.handle('x'*40,req)
                    self.assertEqual(result['status'],'awaiting_approval')
                    self.assertEqual(result['version'],original_package)
                    self.assertIn(wait_metric,prometheus_text(snapshot(service.graph.db)))
                    package=json.loads(service.artifacts.get(result['version'])); report=json.loads(service.artifacts.get(package['test_report']))
                    self.assertEqual(report['commands'][0]['exit_code'],0)
                    self.assertEqual(package['test_evidence_kind'],'supervisor_observed'); self.assertTrue(package['trusted_execution_verified']); self.assertEqual(package['role_report_kind'],'role_reported')
                    observation=json.loads(service.artifacts.get(package['command_evidence']))
                    self.assertEqual(observation['exit_code'],0); self.assertEqual(observation['stdout']['text'],'[redacted]'); self.assertGreater(observation['stdout']['bytes'],0)
                    implement=json.loads(service.artifacts.get(service.graph.get('run-1','implement')['version']))
                    evidence=json.loads(service.artifacts.get(implement['evidence_ref']))
                    self.assertEqual(evidence['head'],git('rev-parse','HEAD')); self.assertEqual(evidence['base'],base)
                    self.assertIn('corrected fixture',service.artifacts.get(evidence['diff_ref']).decode())
                    qa=json.loads(service.artifacts.get(service.graph.get('run-1','qa')['version']))
                    self.assertNotEqual(qa['session_id'],implement['session_id'])
                    class PRHTTP:
                        def __init__(self): self.head='a'*40; self.draft=True; self.comments=[]; self.writes=0; self.fail=False; self.pushed=False; self.created=False
                        def request(self,method,path,body=None):
                            if '/git/matching-refs/heads/' in path: return [{'ref':'refs/heads/task-1','object':{'sha':self.head}}] if self.pushed else []
                            if '/git/ref/heads/' in path: return {'object':{'sha':self.head}}
                            if '/pulls?state=' in path: return [{'number':7,'head':{'ref':'task-1','repo':{'full_name':'owner/repo'}}}] if self.created else []
                            if path.endswith('/pulls/7'):
                                return {'number':7,'node_id':'node','draft':self.draft,'state':'open','title':'Fixture PR','body':'Fixture body','head':{'sha':self.head,'ref':'task-1','repo':{'full_name':'owner/repo'}},'base':{'ref':'main','repo':{'full_name':'owner/repo'}}}
                            if 'check-runs' in path: return {'check_runs':[{'head_sha':self.head,'status':'completed','conclusion':'success'}]}
                            if path.endswith('/status'): return {'state':'success'}
                            if '/pulls/7/comments?' in path: return self.comments
                            if 'comments?' in path or '/reviews?' in path: return []
                            if method=='POST':
                                self.writes+=1; self.created=True
                                if self.fail: raise TimeoutError()
                                return {'number':7,'draft':True,'head':{'sha':self.head}}
                            raise AssertionError(path)
                        def push_exact(self,branch,sha):
                            self.writes+=1
                            if getattr(self,'fail_before',False): raise TimeoutError()
                            self.head=sha; self.pushed=True
                            if self.fail: raise TimeoutError()
                        def mark_ready(self,node):
                            self.writes+=1; self.draft=False
                            if self.fail: raise TimeoutError()
                            return {'id':node,'isDraft':False}
                    class Authority:
                        def __init__(self): self.http=PRHTTP(); self.quiescent=False; self.outcome='desired_state_observed'
                        def ready(self): return True
                        def observe(self,run,action,target,scope):
                            result={'quiescent':self.quiescent,'outcome':self.outcome}
                            if action.startswith(('open_draft:','mark_ready:')): result['remote_receipt']=self.http.request('GET','/repos/owner/repo/pulls/7')
                            return result
                    service.pr_authority=Authority(); service.task_intake.github=service.pr_authority.http
                    base_req={'run':'run-1','queue_claim':q['token'],'graph_claim':g}
                    from runner.control import ControlError
                    import time
                    def approved(kind,parameters):
                        proposal=control.handle('x'*40,{**base_req,'action':'pr_propose','kind':kind,'parameters':parameters})
                        with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':proposal['proposal']})
                        control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':proposal['proposal'],'expires':time.time()+60})
                        return control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':proposal['proposal']})
                    wrong=control.handle('x'*40,{**base_req,'action':'pr_propose','kind':'push','parameters':{'expected_remote':'0'*40}})
                    service.graph.db.execute("CREATE TRIGGER reject_proposal_approval BEFORE UPDATE OF state ON pr_bridge_operations WHEN NEW.state='approved' BEGIN SELECT RAISE(ABORT,'unavailable'); END")
                    with self.assertRaises(ControlError):control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':wrong['proposal'],'expires':time.time()+60})
                    self.assertIsNone(service.graph.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',wrong['action'])).fetchone())
                    self.assertEqual(service.graph.db.execute('SELECT state FROM pr_bridge_operations WHERE proposal=?',(wrong['proposal'],)).fetchone(),('proposed',))
                    self.assertFalse(service.graph.db.execute('SELECT 1 FROM action_authorization_history WHERE run=? AND action=?',('run-1',wrong['action'])).fetchone())
                    service.graph.db.execute('DROP TRIGGER reject_proposal_approval')
                    service.graph.db.execute("CREATE TRIGGER ignore_proposal_approval BEFORE UPDATE OF state ON pr_bridge_operations WHEN NEW.state='approved' BEGIN SELECT RAISE(IGNORE); END")
                    with self.assertRaises(ControlError):control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':wrong['proposal'],'expires':time.time()+60})
                    self.assertIsNone(service.graph.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',wrong['action'])).fetchone())
                    service.graph.db.execute('DROP TRIGGER ignore_proposal_approval')
                    service.graph.db.execute("CREATE TRIGGER expire_job_during_approval BEFORE INSERT ON external_actions WHEN NEW.run='run-1' BEGIN UPDATE jobs SET expires=0 WHERE run=NEW.run; END")
                    with self.assertRaises(ControlError):control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':wrong['proposal'],'expires':time.time()+60})
                    self.assertIsNone(service.graph.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',wrong['action'])).fetchone())
                    service.graph.db.execute('DROP TRIGGER expire_job_during_approval')
                    service.graph.db.execute("CREATE TRIGGER expire_graph_during_approval BEFORE INSERT ON external_actions WHEN NEW.run='run-1' BEGIN UPDATE graph_claims SET expires=0 WHERE run=NEW.run; END")
                    with self.assertRaises(ControlError):control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':wrong['proposal'],'expires':time.time()+60})
                    self.assertIsNone(service.graph.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',wrong['action'])).fetchone())
                    service.graph.db.execute('DROP TRIGGER expire_graph_during_approval')
                    control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':wrong['proposal'],'expires':time.time()+60})
                    service.graph.db.execute("UPDATE pr_bridge_operations SET state='proposed' WHERE proposal=?",(wrong['proposal'],))
                    withdrawal={**base_req,'action':'pr_withdraw','proposal':wrong['proposal']}
                    for state in ('approved','uncertain','observed','unknown'):
                        service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES('other','run-1','draft',?,NULL)",(state,))
                        with self.assertRaises(ControlError):control.handle('x'*40,withdrawal)
                        service.graph.db.execute("DELETE FROM pr_bridge_operations WHERE proposal='other'")
                    service.actions.approve('run-1','fixture-other','v1','owner')
                    service.actions.reserve('run-1','fixture-other','v1')
                    with self.assertRaises(ControlError):control.handle('x'*40,withdrawal)
                    service.graph.db.execute("UPDATE external_actions SET status='unknown' WHERE run='run-1' AND action='fixture-other'")
                    with self.assertRaises(ControlError):control.handle('x'*40,withdrawal)
                    service.graph.db.execute("UPDATE external_actions SET status='uncertain' WHERE run='run-1' AND action='fixture-other'")
                    service.actions.resolve('run-1','fixture-other','v1','not_executed')
                    manifest=service.graph.db.execute('SELECT manifest FROM action_bindings WHERE run=? AND action=?',('run-1',wrong['action'])).fetchone()[0]
                    service.graph.db.execute('UPDATE action_bindings SET manifest=? WHERE run=? AND action=?',('{}','run-1',wrong['action']))
                    with self.assertRaises(ControlError):control.handle('x'*40,withdrawal)
                    service.graph.db.execute('UPDATE action_bindings SET manifest=? WHERE run=? AND action=?',(manifest,'run-1',wrong['action']))
                    self.assertTrue(control.handle('x'*40,withdrawal)['revoked'])
                    self.assertEqual(service.graph.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',wrong['action'])).fetchone(),('revoked',))
                    self.assertEqual(service.graph.db.execute('SELECT reason FROM pr_reauthorization_history WHERE proposal=? ORDER BY id DESC LIMIT 1',(wrong['proposal'],)).fetchone(),('orphaned_approval_withdrawn',))
                    control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':wrong['proposal'],'expires':time.time()+60})
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':wrong['proposal']})
                    nonexecution=control.handle('x'*40,{**base_req,'action':'pr_reauthorize','proposal':wrong['proposal']})
                    self.assertEqual(nonexecution['reason'],'confirmed_not_executed')
                    self.assertEqual(service.graph.db.execute('SELECT state FROM pr_bridge_operations WHERE proposal=?',(wrong['proposal'],)).fetchone(),('proposed',))
                    initial=control.handle('x'*40,{**base_req,'action':'pr_propose','kind':'push','parameters':{'expected_remote':'a'*40}})
                    control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':initial['proposal'],'expires':time.time()+60})
                    withdrawal={**base_req,'action':'pr_withdraw','proposal':initial['proposal']}
                    with self.assertRaises(ControlError):control.handle('wrong',withdrawal)
                    with self.assertRaises(ControlError):control.handle('x'*40,{**withdrawal,'queue_claim':'stale'})
                    audit_trigger=service.graph.db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='action_audit_update'").fetchone()[0]
                    service.graph.db.execute('DROP TRIGGER action_audit_update')
                    with self.assertRaises(ControlError):control.handle('x'*40,withdrawal)
                    self.assertEqual(service.graph.db.execute('SELECT state FROM pr_bridge_operations WHERE proposal=?',(initial['proposal'],)).fetchone(),('approved',))
                    service.graph.db.execute(audit_trigger)
                    service.graph.db.execute("CREATE TRIGGER reject_withdrawal BEFORE INSERT ON pr_reauthorization_history BEGIN SELECT RAISE(IGNORE); END")
                    with self.assertRaises(ControlError):control.handle('x'*40,withdrawal)
                    self.assertEqual(service.graph.db.execute('SELECT state FROM pr_bridge_operations WHERE proposal=?',(initial['proposal'],)).fetchone(),('approved',))
                    service.graph.db.execute('DROP TRIGGER reject_withdrawal')
                    self.assertTrue(control.handle('x'*40,withdrawal)['revoked'])
                    with self.assertRaises(ControlError):control.handle('x'*40,withdrawal)
                    self.assertEqual(service.graph.db.execute('SELECT state FROM pr_bridge_operations WHERE proposal=?',(initial['proposal'],)).fetchone(),('proposed',))
                    self.assertEqual(service.graph.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',initial['action'])).fetchone(),('revoked',))
                    with self.assertRaises(ControlError):control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':initial['proposal']})
                    service.model_supervisor=SimpleNamespace(db=root+'/state.db')
                    checkpoint(service,'run-1',q['token'],g)
                    service.lifecycle.register_checkpoint_replacement('run-1','inc-next','inc-third')
                    service.lifecycle.close();service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc-third')
                    q=service.lifecycle.claim();g=service.claim('run-1');service.model_supervisor=None
                    base_req.update(queue_claim=q['token'],graph_claim=g);req.update(queue_claim=q['token'],graph_claim=g)
                    control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':initial['proposal'],'expires':time.time()+60})
                    service.graph.db.execute('UPDATE action_bindings SET expires=0 WHERE run=? AND action=?',('run-1',initial['action']))
                    expired=control.handle('x'*40,{**base_req,'action':'pr_reauthorize','proposal':initial['proposal']})
                    self.assertEqual(expired['reason'],'expired')
                    self.assertEqual(service.graph.db.execute('SELECT state FROM pr_bridge_operations WHERE proposal=?',(initial['proposal'],)).fetchone(),('proposed',))
                    control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':initial['proposal'],'expires':time.time()+60})
                    service.pr_authority.http.fail_before=True
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':initial['proposal']})
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_reauthorize','proposal':initial['proposal']})
                    with self.assertRaises(ControlError):control.handle('x'*40,{**base_req,'action':'pr_withdraw','proposal':initial['proposal']})
                    service.pr_authority.quiescent=True; service.pr_authority.outcome='not_executed'
                    result=control.handle('x'*40,{**base_req,'action':'pr_reconcile','proposal':initial['proposal']})
                    self.assertEqual(result['outcome'],'not_executed')
                    self.assertEqual(control.handle('x'*40,{**base_req,'action':'pr_reauthorize','proposal':initial['proposal']})['reason'],'confirmed_not_executed')
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':initial['proposal']})
                    control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':initial['proposal'],'expires':time.time()+60})
                    service.pr_authority.http.fail_before=False; service.pr_authority.quiescent=False; service.pr_authority.outcome='desired_state_observed'
                    service.pr_authority.http.fail=True
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':initial['proposal']})
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':initial['proposal']})
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_reauthorize','proposal':initial['proposal']})
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_reconcile','proposal':initial['proposal']})
                    service.pr_authority.quiescent=True; service.pr_authority.http.fail=False
                    push_observed=control.handle('x'*40,{**base_req,'action':'pr_reconcile','proposal':initial['proposal']})
                    self.assertEqual(push_observed['outcome'],'desired_state_observed')
                    self.assertEqual(service.graph.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',initial['action'])).fetchone(),('desired_state_observed',))
                    approved_head=service.pr_authority.http.head
                    service.pr_authority.http.head='f'*40
                    self.assertFalse(service.task_intake.revalidate('run-1'))
                    service.pr_authority.http.head=approved_head
                    draft=control.handle('x'*40,{**base_req,'action':'pr_propose','kind':'draft','parameters':{'title':'Fixture PR','body':'Fixture body'}})
                    control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':draft['proposal'],'expires':time.time()+60})
                    service.pr_authority.http.fail=True; service.pr_authority.quiescent=False
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':draft['proposal']})
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_reconcile','proposal':draft['proposal']})
                    service.pr_authority.quiescent=True; service.pr_authority.http.fail=False
                    draft_observed=control.handle('x'*40,{**base_req,'action':'pr_reconcile','proposal':draft['proposal']})
                    self.assertEqual(draft_observed['outcome'],'desired_state_observed')
                    self.assertEqual(service.graph.db.execute('SELECT state FROM pr_bridge_operations WHERE proposal=?',(draft['proposal'],)).fetchone(),('desired_state_observed',))
                    self.assertEqual(service.graph.get('run-1','open_draft')['status'],'passed')
                    self.assertNotIn('stage="open_draft",status="awaiting_approval"',prometheus_text(snapshot(service.graph.db)))
                    service.pr_authority.http.comments=[{'id':1,'body':'Fix this'}]
                    service.graph.db.execute("CREATE TRIGGER reject_review_audit BEFORE INSERT ON review_audit BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END")
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_review','number':7,'dispositions':{'review:1':{'disposition':'valid','evidence':'fixture'}}})
                    self.assertEqual(service.graph.db.execute("SELECT COUNT(*) FROM pr_review_blockers WHERE run='run-1'").fetchone()[0],0)
                    service.graph.db.execute('DROP TRIGGER reject_review_audit')
                    blocked=control.handle('x'*40,{**base_req,'action':'pr_review','number':7,'dispositions':{'review:1':{'disposition':'valid','evidence':'fixture'}}})
                    self.assertEqual(blocked['status'],'blocked_for_correction')
                    service.pr_authority.http.comments=[]
                    still_blocked=control.handle('x'*40,{**base_req,'action':'pr_review','number':7,'dispositions':{}})
                    self.assertEqual(still_blocked['status'],'blocked_for_correction')
                    self.assertEqual(json.loads(service.artifacts.get(still_blocked['evidence_ref']))['status'],'blocked_for_correction')
                    service.pr_authority.http.comments=[{'id':1,'body':'Fix this'}]
                    requested=control.handle('x'*40,{**base_req,'action':'pr_request_qa','evidence_ref':blocked['evidence_ref']})
                    self.assertEqual(requested['status'],'blocked')
                    old_head=service.pr_authority.http.head
                    for expected in ('implement','test','qa'):
                        revised=control.handle('x'*40,req); self.assertEqual(revised['stage'],expected)
                    self.assertNotEqual(git('rev-parse','HEAD'),old_head)
                    approved('push',{'expected_remote':old_head})
                    service.pr_authority.http.comments=[]
                    ready=control.handle('x'*40,{**base_req,'action':'pr_review','number':7,'dispositions':{}})
                    self.assertEqual(ready['status'],'ready_for_approval')
                    ready_proposal=control.handle('x'*40,{**base_req,'action':'pr_propose','kind':'ready','parameters':{'number':7,'node':'node','digest':ready['digest'],'dispositions':{}}})
                    control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':ready_proposal['proposal'],'expires':time.time()+60})
                    service.pr_authority.http.fail=True; service.pr_authority.quiescent=False
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_execute','proposal':ready_proposal['proposal']})
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_reconcile','proposal':ready_proposal['proposal']})
                    service.pr_authority.http.fail=False; service.pr_authority.quiescent=True
                    self.assertFalse(control.handle('x'*40,{**base_req,'action':'pr_reconcile','proposal':ready_proposal['proposal']})['inspection_required'])
                    self.assertTrue(service.actions.pending_followups('run-1'))
                    second=control.handle('x'*40,{**base_req,'action':'pr_post_ready','number':7,'node':'node','dispositions':{}})
                    self.assertTrue(second['digest']); self.assertEqual(second['status'],'human_merge_review'); self.assertEqual(service.pr_authority.http.writes,5)
                    self.assertEqual(control.handle('x'*40,req)['status'],'awaiting_merge_review')
                    service.pr_authority.http.comments=[{'id':2,'body':'New comment'}]
                    with self.assertRaises(ControlError): control.handle('x'*40,req)
                    service.pr_authority.http.comments=[]
                    self.assertEqual(control.handle('x'*40,req)['status'],'awaiting_merge_review')
                    stale=control.handle('x'*40,{**base_req,'action':'pr_propose','kind':'push','parameters':{'expected_remote':service.pr_authority.http.head}})
                    (workspace/'new.txt').write_text('changed after review'); git('add','new.txt'); git('commit','-m','stale change')
                    with self.assertRaises(ControlError): control.handle('x'*40,{**base_req,'action':'pr_approve','proposal':stale['proposal'],'expires':time.time()+60})
                    self.assertEqual(service.pr_authority.http.writes,5)
                    from runner.control import ControlError
                    with self.assertRaises(ControlError): control.handle('x'*40,req)
                finally: service.lifecycle.close(); service.close(); engine.shutdown(); engine_thread.join(); engine.server_close()
                import sqlite3
                from contextlib import closing
                with closing(sqlite3.connect(root+'/state.db')) as reopened:
                    history=reopened.execute("SELECT status,evidence_ref FROM review_audit WHERE run='run-1' AND comment_id='review:1' ORDER BY id").fetchall()
                    self.assertEqual(history,[('open',blocked['evidence_ref']),('resolved',blocked['evidence_ref'])])
                    for proposal in (draft,ready_proposal):
                        action_history=reopened.execute("SELECT status FROM action_audit WHERE run='run-1' AND action=? ORDER BY id",(proposal['action'],)).fetchall()
                        self.assertEqual(action_history,[('approved',),('uncertain',),('desired_state_observed',)])
                    self.assertTrue(reopened.execute("SELECT 1 FROM action_audit WHERE run='run-1' AND status='approved'").fetchone())
                    self.assertTrue(reopened.execute("SELECT 1 FROM execution_turn_audit WHERE run='run-1' AND role='qa'").fetchone())
                    self.assertTrue(reopened.execute("SELECT 1 FROM command_audit WHERE run='run-1' AND status='passed' AND record_ref IS NOT NULL").fetchone())
