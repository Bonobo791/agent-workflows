import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from runner.local_scope import LocalScopes
from runner.graph import GraphError
from runner.local_supervisor import LocalSupervisor
from runner.manual_lifecycle import ManualLifecycle
from runner.openfilm_platform import verify_platform
from runner.worker import build_control
from tests.test_local_scope import Backend

class PlatformReviewerTests(unittest.TestCase):
    def test_platform_review_binds_sources_turn_scope_and_exact_brief(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);skills=root/'skills'/'platform';skills.mkdir(parents=True);(skills/'SKILL.md').write_text('Review platform requirements.')
            binary=root/'codex';binary.write_text('synthetic');binary.chmod(0o700);backend=Backend()
            factory=lambda service:LocalSupervisor(str(root/'state.db'),str(binary),{'HOME':str(root)},backend,lambda run:True,lambda run,role:str(root),lambda:True,{'mcp_servers':[],'plugins':[]},policy_hash='a'*64)
            config={'sources':{'linkedin':{'url':'https://example.com/spec','surface':'page logo'}}}
            service,_=build_control(str(root),'x'*40,execution_mode='local',local_supervisor_factory=factory,lifecycle_factory=lambda _:ManualLifecycle(str(root/'state.db'),'worker','inc'),role_config={'skill_roots':[str(root/'skills')],'required_skills':{'platform_review':['platform']}},platform_config=config)
            try:
                service.model_policy.discover(lambda *_:{'data':[{'model':'fake-terra','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None})
                service.create('run','marketing','task');service.enqueue('run');job=service.lifecycle.claim();claim=service.claim('run')
                brief={'assets':[{'channel':'linkedin','kind':'png','width':400,'height':400,'duration_seconds':None}]};brief_ref=service.artifacts.put(json.dumps(brief).encode())
                authority=service.openfilm_platform_authority
                def fetch(url,**_):
                    data=b'official platform source';return {'url':url,'content_type':'text/plain','content':data.decode(),'sha256':hashlib.sha256(data).hexdigest()}
                class Executor:
                    supported=True;blocked=False;counter=0
                    def __init__(self,supervisor,provenance,artifacts,fence,model_policy=None): self.provenance=provenance;self.artifacts=artifacts;self.supervisor=supervisor
                    def review(self,**kw):
                        Executor.counter+=1;session='session-'+str(Executor.counter)
                        assert 'Return only passed or blocked' in kw['prompt']
                        assert 'Each finding evidence must exactly match one SHA-256 string in input_versions' in kw['prompt']
                        payload=json.loads(kw['prompt'].split('\n',1)[1]);assert payload['skills']=={'platform':'Review platform requirements.'};source=payload['sources'][0]['content_ref'];spec=brief['assets'][0]
                        report={'assets':[{**spec,'source_ref':source,'supported':Executor.supported}]};out=self.artifacts.put(json.dumps(report).encode())
                        finding={'id':'f1','severity':'blocking','description':'The source omits a height limit.','location':'asset','evidence':kw['input_versions'][2],'remedy':'Supply current requirements.','resolution':'open'}
                        value={'stage':'platform_review','status':'blocked' if Executor.blocked else 'passed','input_versions':kw['input_versions'],'outputs':[out],'findings':[finding] if Executor.blocked else []}
                        if Executor.blocked:value['reason']='Exact dimensions are unverified.'
                        response=json.dumps(value);response_ref=self.artifacts.put(response.encode())
                        self.provenance.bind('run','platform_review',session,kw['skills'],claim,fresh=True,subject=brief_ref);self.provenance.mark_completed('run','platform_review',session,claim,response_ref)
                        db=self.provenance.graph.db;db.execute('CREATE TABLE IF NOT EXISTS completed_turn_outputs(run TEXT,role TEXT,session TEXT,turn TEXT,input_versions TEXT,output_refs TEXT)')
                        db.execute('INSERT INTO completed_turn_outputs VALUES(?,?,?,?,?,?)',('run','platform_review',session,'turn',json.dumps(kw['input_versions']),json.dumps([out])))
                        scopes=LocalScopes(str(root/'state.db'),backend);unit=scopes.reserve('run');backend.unit=unit;backend.state='active';scopes.bind('run',unit);scopes.stop('run',unit);scopes.close()
                        binding=self.supervisor._binding('run','platform_review',kw['skills'],kw['model'])[1]
                        db.execute('INSERT INTO local_turn_scopes VALUES(?,?,?,?,?,?)',('run','platform_review',session,'turn',unit,binding))
                        return {'output':response,'outputs':[out],'response_ref':response_ref,'session_id':session,'turn_id':'turn'}
                with patch.object(authority.fetcher,'fetch',side_effect=fetch),patch('runner.platform_reviewer.CodexExecutor',Executor):
                    proof=authority.check_for_claim('run',brief_ref,brief,claim,job['token'])
                self.assertTrue(authority.verify('run',brief_ref,brief,proof))
                service.graph.db.execute("UPDATE local_scopes SET invocation=NULL WHERE unit=(SELECT unit FROM local_turn_scopes WHERE session='session-1')")
                self.assertFalse(authority.verify('run',brief_ref,brief,proof))
                service.graph.db.execute("UPDATE local_scopes SET invocation=? WHERE unit=(SELECT unit FROM local_turn_scopes WHERE session='session-1')",('a'*32,))
                self.assertTrue(verify_platform(service,'run',brief_ref,brief,proof,5,lambda:False))
                Executor.blocked=True;Executor.supported=False
                with patch.object(authority.fetcher,'fetch',side_effect=fetch),patch('runner.platform_reviewer.CodexExecutor',Executor):
                    blocked=authority.check_for_claim('run',brief_ref,brief,claim,job['token'])
                self.assertEqual(set(blocked),{'blocked_ref'})
                self.assertTrue(authority.verify_blocked('run',brief_ref,brief,blocked['blocked_ref']))
                service.graph.db.execute("UPDATE local_scopes SET invocation=NULL WHERE unit=(SELECT unit FROM local_turn_scopes WHERE session='session-2')")
                self.assertFalse(authority.verify_blocked('run',brief_ref,brief,blocked['blocked_ref']))
                service.graph.db.execute("UPDATE local_scopes SET invocation=? WHERE unit=(SELECT unit FROM local_turn_scopes WHERE session='session-2')",('a'*32,))
                self.assertFalse(authority.verify('run',brief_ref,brief,blocked['blocked_ref']))
                self.assertFalse(authority.verify_blocked('other',brief_ref,brief,blocked['blocked_ref']))
                evidence=json.loads(service.artifacts.get(blocked['blocked_ref']))
                self.assertEqual(evidence['findings'][0]['description'],'The source omits a height limit.')
                service.graph.db.execute("UPDATE local_scopes SET state='running' WHERE unit=(SELECT unit FROM local_turn_scopes WHERE session='session-2')")
                self.assertFalse(authority.verify_blocked('run',brief_ref,brief,blocked['blocked_ref']))
                service.graph.db.execute("UPDATE local_scopes SET state='terminated' WHERE unit=(SELECT unit FROM local_turn_scopes WHERE session='session-2')")
                Executor.blocked=False
                Executor.supported=False
                with patch.object(authority.fetcher,'fetch',side_effect=fetch),patch('runner.platform_reviewer.CodexExecutor',Executor):
                    with self.assertRaises(ValueError): authority.check_for_claim('run',brief_ref,brief,claim,job['token'])
                changed={'assets':[dict(brief['assets'][0],width=401)]}
                self.assertFalse(authority.verify('run',brief_ref,changed,proof))
                unknown={'assets':[dict(brief['assets'][0],channel='x')]};unknown_ref=service.artifacts.put(json.dumps(unknown).encode())
                with patch.object(authority.fetcher,'fetch') as network:
                    with self.assertRaises(ValueError): authority.check_for_claim('run',unknown_ref,unknown,claim,job['token'])
                    network.assert_not_called()
                for mutation in ("UPDATE jobs SET status='cancelling' WHERE run=NEW.run", "UPDATE graph_claims SET expires=0 WHERE run=NEW.run", "UPDATE platform_reviews SET policy_hash='altered' WHERE proof=NEW.proof"):
                    service.graph.db.execute(f'CREATE TRIGGER corrupt_platform AFTER INSERT ON platform_reviews BEGIN {mutation}; END')
                    try:
                        for is_blocked in (False,True):
                            Executor.blocked=is_blocked;Executor.supported=not is_blocked
                            before=service.graph.db.execute('SELECT count(*) FROM platform_reviews').fetchone()[0]
                            with patch.object(authority.fetcher,'fetch',side_effect=fetch),patch('runner.platform_reviewer.CodexExecutor',Executor):
                                with self.assertRaises((ValueError,GraphError)): authority.check_for_claim('run',brief_ref,brief,claim,job['token'])
                            self.assertEqual(service.graph.db.execute("SELECT status FROM jobs WHERE run='run'").fetchone()[0],'running')
                            self.assertEqual(service.graph.db.execute('SELECT count(*) FROM platform_reviews').fetchone()[0],before)
                    finally: service.graph.db.execute('DROP TRIGGER corrupt_platform')
                service.graph.db.execute("UPDATE local_scopes SET state='running' WHERE unit=(SELECT unit FROM local_turn_scopes WHERE session='session-1')")
                self.assertFalse(authority.verify('run',brief_ref,brief,proof))
            finally:service.lifecycle.close();service.close()
