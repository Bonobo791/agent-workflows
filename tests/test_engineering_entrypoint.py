import http.client
import http.server
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from runner.local_command import _claim_command_launch
from runner.local_scope import LocalScopes, reserve_for_execution
from runner.local_supervisor import LocalSupervisor
from runner.manual_lifecycle import ManualLifecycle
from runner.process_transport import ProcessTransport
from runner.worker import build_control

FIXTURE = Path(__file__).resolve().parent/'fixtures'/'fake_engineering_codex.py'

class ScopeBackend:
    """Synthetic user-scope backend that tracks several scope units at once."""
    def __init__(self): self.units={}
    def start(self,unit): self.units[unit]={'active':'active','invocation':secrets.token_hex(16),'cgroup':'/user.slice/'+unit}
    def inspect(self,unit):
        info=self.units.get(unit)
        if info is None: return {'id':unit,'load':'not-found','active':'inactive','invocation':'','cgroup':''}
        live=info['active']=='active'
        return {'id':unit,'load':'loaded' if live else 'not-found','active':info['active'],'invocation':info['invocation'] if live else '','cgroup':info['cgroup'] if live else ''}
    def stop(self,unit): self.units[unit]['active']='inactive'; return True
    def empty_cgroup(self,path): return True

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
            workspace=Path(root)/'workspaces'/'run-1'/'repo'; workspace.mkdir(parents=True,mode=0o700)
            (Path(root)/'workspaces').chmod(0o700); workspace.parent.chmod(0o700)
            def git(*args): return subprocess.run(['git',*args],cwd=workspace,check=True,capture_output=True,text=True).stdout.strip()
            git('init','-b','main'); (workspace/'.git').chmod(0o700); git('config','user.name','Fixture'); git('config','user.email','fixture@example.invalid')
            (workspace/'base.txt').write_text('base'); git('add','base.txt'); git('commit','-m','base'); base=git('rev-parse','HEAD'); git('checkout','-b','task-1'); git('remote','add','origin','https://github.com/owner/repo.git')
            skillroot=Path(root)/'skills'; (skillroot/'role').mkdir(parents=True); (skillroot/'role'/'SKILL.md').write_text('Synthetic role')
            state_path=Path(root)/'codex-state.json'
            state_path.write_text(json.dumps({'counter':0,'threads':{},'canonical_reviews':0,'resumed':[],'errors':[]}))
            def read_state(): return json.loads(state_path.read_text())
            outputs=Path(root)/'outputs'; outputs.mkdir(mode=0o700)
            binary=Path(root)/'codex'; binary.write_text('synthetic'); binary.chmod(0o700)
            scope_backend=ScopeBackend()
            class Handle:
                def __init__(self,db,run,path,workspace_claim):
                    self.db,self.run=db,run; self.output_dir=tempfile.mkdtemp(dir=outputs); self.output_path_prefix=self.output_dir
                    self.unit=reserve_for_execution(db,run,scope_backend,path,workspace_claim)
                    scope_backend.start(self.unit); scopes=LocalScopes(db,scope_backend)
                    try: scopes.bind(run,self.unit)
                    finally: scopes.close()
                    self.process=subprocess.Popen([sys.executable,str(FIXTURE)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,env={**os.environ,'OUTPUT':self.output_dir,'FAKE_STATE':str(state_path),'FAKE_WORKSPACE':path})
                def assert_active(self): return True
                def open_transport(self,deadline,cancelled): return ProcessTransport(self.process,deadline,cancelled)
                def verify_config(self,request): return True
                def finish(self):
                    self.process.stdin.close(); self.process.wait(timeout=10); self.process.stdout.close()
                    scopes=LocalScopes(self.db,scope_backend)
                    try: return scopes.stop(self.run,self.unit)
                    finally: scopes.close()
                def cancel(self): return self.finish()
            def workspace_for(run,role): return str(Path(root)/'workspaces'/run/'repo') if role in ('implement','test','qa') else str(Path(root)/'workspaces'/role)
            factory=lambda service:LocalSupervisor(str(Path(root)/'state.db'),str(binary),{'HOME':root},scope_backend,lambda run:True,workspace_for,lambda:True,{'mcp_servers':[],'plugins':[]},engineering_root=root,policy_hash='a'*64)
            def sandbox(supervisor,run,path,argv,deadline,output_limit,on_reserved,*,reserved_command=None,**_):
                unit=_claim_command_launch(supervisor,run,path,reserved_command,None)
                scope_backend.start(unit); scopes=LocalScopes(supervisor.db,scope_backend)
                try:
                    scopes.bind(run,unit)
                    completed=subprocess.run(argv,cwd=path,env={'PATH':'/usr/bin:/bin'},capture_output=True,timeout=10,check=False)
                    scopes.stop(run,unit)
                finally: scopes.close()
                return {'outcome':'exited','exit_code':completed.returncode,'stdout':completed.stdout,'stderr':completed.stderr,'unit':unit}
            task={'run':'run-1','workflow':'engineering','project':'DEMO','number':1,'ready_columns':[10],'completed_columns':[20],'blocker_direction':'inward','plan_endpoint':'https://plan.example.invalid/mcp','github_owner':'owner','github_repo':'repo','github_branch':'task-1','github_base':'main'}
            roles=['plan','implement','test','qa']
            command={'argv':['/usr/bin/python3','-c','print("fixture test passed")'],'timeout_seconds':10,'output_limit':4096}
            with patch('runner.plan_mcp.HTTPSMCP',return_value=PlanHTTP()),patch('workflows.engineering.github.HTTPSGitHub',return_value=GithubHTTP()),patch('runner.local_supervisor.start_local_process',side_effect=lambda db,run,path,*args,workspace_claim=None,**kwargs:Handle(db,run,path,workspace_claim)),patch('runner.local_command.run_scoped_sandbox',side_effect=sandbox):
                service,control=build_control(root,'x'*40,execution_mode='local',local_supervisor_factory=factory,command_config=command,task_config=task,plan_token='synthetic-plan',github_token='synthetic-github',role_config={'skill_roots':[str(skillroot)],'required_skills':{role:['role'] for role in roles}},lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
                try:
                    service.model_supervisor.preflight=lambda:True
                    service.approve('controller','command_policy',service.command_supervisor.policy_hash,'operator')
                    control.handle('x'*40,{'action':'submit_configured'}); q=control.handle('x'*40,{'action':'manual_claim'})['claim']; g=control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
                    binding={'action':'engineering_workspace_binding','run':'run-1','queue_claim':q['token'],'graph_claim':g,'base':base}
                    proposal=control.handle('x'*40,binding); binding.update(action='bind_engineering_workspace',binding_hash=proposal['binding_hash']); control.handle('x'*40,binding)
                    req={'action':'dispatch_stage','run':'run-1','queue_claim':q['token'],'graph_claim':g}
                    seen=[]
                    for _ in range(10):
                        result=control.handle('x'*40,req); seen.append(result.get('stage'))
                        if result.get('status')=='awaiting_approval': break
                    self.assertEqual(seen,['intake','plan','implement','test','qa','implement','test','qa','open_draft'])
                    self.assertEqual(result['status'],'awaiting_approval'); self.assertEqual(read_state()['canonical_reviews'],2); self.assertEqual(len(read_state()['resumed']),1)
                    from runner.metrics import snapshot, prometheus_text
                    wait_metric='workflow_stages{workflow="engineering",stage="open_draft",status="awaiting_approval"} 1'
                    self.assertIn(wait_metric,prometheus_text(snapshot(service.graph.db)))
                    original_package=result['version'];old_queue,old_graph=q['token'],g
                    from runner.checkpoint import checkpoint
                    from tests.test_local_scope import Backend
                    scopes=LocalScopes(root+'/state.db',Backend());scopes.close()
                    checkpoint(service,'run-1',old_queue,old_graph)
                    self.assertNotIn(wait_metric,prometheus_text(snapshot(service.graph.db)))
                    service.lifecycle.register_checkpoint_replacement('run-1','inc','inc-next')
                    service.lifecycle.close();service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc-next')
                    q=service.lifecycle.claim();g=service.claim('run-1')
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
                    approval={**base_req,'action':'pr_approve','proposal':wrong['proposal'],'expires':time.time()+60}
                    failures=("BEFORE UPDATE OF state ON pr_bridge_operations WHEN NEW.state='approved' BEGIN SELECT RAISE(ABORT,'unavailable'); END",
                              "BEFORE UPDATE OF state ON pr_bridge_operations WHEN NEW.state='approved' BEGIN SELECT RAISE(IGNORE); END",
                              "BEFORE INSERT ON external_actions WHEN NEW.run='run-1' BEGIN UPDATE jobs SET expires=0 WHERE run=NEW.run; END",
                              "BEFORE INSERT ON external_actions WHEN NEW.run='run-1' BEGIN UPDATE graph_claims SET expires=0 WHERE run=NEW.run; END",
                              "BEFORE UPDATE OF state ON pr_bridge_operations WHEN NEW.state='approved' BEGIN UPDATE jobs SET expires=0 WHERE run=NEW.run; END",
                              "BEFORE UPDATE OF state ON pr_bridge_operations WHEN NEW.state='approved' BEGIN UPDATE graph_claims SET expires=0 WHERE run=NEW.run; END")
                    for failure in failures:
                        with self.subTest(approval_failure=failure):
                            service.graph.db.execute('CREATE TRIGGER reject_proposal_approval '+failure)
                            with self.assertRaises(ControlError):control.handle('x'*40,approval)
                            self.assertIsNone(service.graph.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',wrong['action'])).fetchone())
                            self.assertEqual(service.graph.db.execute('SELECT state FROM pr_bridge_operations WHERE proposal=?',(wrong['proposal'],)).fetchone(),('proposed',))
                            self.assertFalse(service.graph.db.execute('SELECT 1 FROM action_authorization_history WHERE run=? AND action=?',('run-1',wrong['action'])).fetchone())
                            service.graph.db.execute('DROP TRIGGER reject_proposal_approval')
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
                    for table in ('jobs','graph_claims'):
                        with self.subTest(withdrawal_expiry=table):
                            service.graph.db.execute("CREATE TRIGGER expire_withdrawal BEFORE INSERT ON pr_reauthorization_history BEGIN UPDATE "+table+" SET expires=0 WHERE run='run-1'; END")
                            with self.assertRaises(ControlError):control.handle('x'*40,withdrawal)
                            self.assertEqual(service.graph.db.execute('SELECT state FROM pr_bridge_operations WHERE proposal=?',(initial['proposal'],)).fetchone(),('approved',))
                            self.assertEqual(service.graph.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',initial['action'])).fetchone(),('approved',))
                            service.graph.db.execute('DROP TRIGGER expire_withdrawal')
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
                    checkpoint(service,'run-1',q['token'],g)
                    service.lifecycle.register_checkpoint_replacement('run-1','inc-next','inc-third')
                    service.lifecycle.close();service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc-third')
                    q=service.lifecycle.claim();g=service.claim('run-1')
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
                finally: service.lifecycle.close(); service.close()
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
