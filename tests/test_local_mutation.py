import hashlib,json,os,time,unittest
from unittest.mock import patch
from tests.test_engineering_workspace import EngineeringWorkspaceTests
from tests.test_local_scope import Backend
from runner.local_config import local_supervisor_factory
from runner.local_scope import LocalScopes
from runner.local_mutation import LocalMutationTransport
from runner.gh_read import GHRead
from runner.engineering_dispatch import workspace_snapshot
from workflows.engineering.review_loop import BoundLedger
from workflows.engineering.github import GitHub

class MutationTests(EngineeringWorkspaceTests):
    def test_draft_requires_exact_approval_and_sends_body_over_private_pipe(self):
        self._exercise_mutation(False)
    def test_mutation_with_invalid_terminal_scope_stays_uncertain(self):
        self._exercise_mutation(True)
    def _exercise_mutation(self,corrupt):
        from runner.local_mutation import LocalMutationError
        service=self.service;service.create('run-1','engineering','task');service.enqueue('run-1');queue=service.lifecycle.claim();claim=service.claim('run-1')
        binary=self.root/'codex';binary.write_bytes(b'synthetic');binary.chmod(0o700)
        config={'codex_executable':str(binary),'codex_sha256':hashlib.sha256(b'synthetic').hexdigest(),'disabled_integrations':{'mcp_servers':[],'plugins':[]}}
        supervisor=local_supervisor_factory(config,{'HOME':str(self.root)},str(self.root))(service);service.model_supervisor=supervisor;supervisor.fence=lambda _:True;engine=Backend();supervisor.backend=engine
        binding=('run-1','owner/repo',self.base,'task-1',os.geteuid(),supervisor.policy_hash);self.registry.register(*binding,self.registry.binding_hash(*binding))
        current=workspace_snapshot(service,'run-1');target={'task':'task','repo':'owner/repo','branch':'task-1','base':'main','head':current['head'],'diff':hashlib.sha256(current['diff'].encode()).hexdigest()}
        ghpath=self.root/'gh';ghpath.write_text('#!/bin/sh\nexit 0\n');ghpath.chmod(0o700);gh=GHRead(str(ghpath),{'HOME':str(self.root)})
        transport=LocalMutationTransport(service,'run-1',queue['token'],claim,gh);github=GitHub(None,'owner','repo');action=github.draft_action('task-1','main','Title','Body');scope='github:owner/repo:branch:task-1'
        ledger=BoundLedger(str(self.root/'state.db'),lambda run:dict(target),lambda run:True);self.addCleanup(ledger.db.close)
        payload={'head':'task-1','base':'main','title':'Title','body':'Body','draft':True}
        with patch('runner.local_supervisor.sandbox_preflight',return_value=True),patch('runner.local_supervisor.scope_preflight',return_value=True):
            with self.assertRaises(Exception):transport.execute('draft','owner/repo',payload)
            ledger.approve_bound('run-1',action,target,'owner',time.time()+60);ledger.reserve_scoped('run-1',action,target['head'],scope)
            proposal=service.artifacts.put(json.dumps({'run':'run-1','kind':'draft','action':action,'target':target,'args':{'title':'Title','body':'Body'}}).encode());service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(proposal,'run-1','draft'))
            def run(s,run,workspace,argv,env,deadline,limit,on_reserved,on_started=None,*,input_data=None):
                self.assertNotIn('Body',str(argv));self.assertEqual(json.loads(input_data),payload);self.assertNotIn('GH_TOKEN',env)
                scopes=LocalScopes(s.db,engine)
                try:
                    unit=scopes.reserve(run);on_reserved(unit);engine.unit=unit;engine.state='active';scopes.bind(run,unit);on_started(unit);scopes.stop(run,unit)
                    return {'outcome':'exited','exit_code':0,'stdout':json.dumps({'number':7,'draft':True,'head':{'sha':target['head']}}).encode(),'stderr':b'','unit':unit}
                finally:scopes.close()
            with patch('runner.local_command._run_scoped',side_effect=run) as launched:
                with self.assertRaises(Exception):transport.execute('draft','owner/repo',{**payload,'body':'tampered'})
                self.assertEqual(launched.call_count,0)
                self.assertEqual(transport.execute('draft','owner/repo',payload),{'number':7,'draft':True,'head':{'sha':target['head']}})
                self.assertEqual(launched.call_count,1)
            ledger.resolve_scoped('run-1',action,target['head'],scope,'executed')
            ready_args={'number':7,'node':'node','digest':'a'*64,'dispositions':{}};ready_action=github.ready_action(7,'node','a'*64);ready_scope='github:owner/repo:pr:7'
            ledger.approve_bound('run-1',ready_action,target,'owner',time.time()+60);ledger.reserve_scoped('run-1',ready_action,target['head'],ready_scope)
            ready_proposal=service.artifacts.put(json.dumps({'run':'run-1','kind':'ready','action':ready_action,'target':target,'args':ready_args}).encode());service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(ready_proposal,'run-1','ready'))
            def run_ready(s,run,workspace,argv,env,deadline,limit,on_reserved,on_started=None,*,input_data=None):
                self.assertEqual(json.loads(input_data)['variables'],{'id':'node'});self.assertNotIn('node',argv)
                scopes=LocalScopes(s.db,engine)
                try:
                    unit=scopes.reserve(run);on_reserved(unit);engine.unit=unit;engine.state='active';scopes.bind(run,unit);on_started(unit);scopes.stop(run,unit)
                    if corrupt:scopes.db.execute('UPDATE local_scopes SET invocation=NULL WHERE unit=?',(unit,))
                    return {'outcome':'exited','exit_code':0,'stdout':json.dumps({'data':{'markPullRequestReadyForReview':{'pullRequest':{'id':'node','isDraft':False}}}}).encode(),'stderr':b'','unit':unit}
                finally:scopes.close()
            with patch('runner.local_command._run_scoped',side_effect=run_ready) as launched:
                with self.assertRaises(Exception):transport.execute('ready','owner/repo',{'node':'other'})
                self.assertEqual(launched.call_count,0)
                if corrupt:
                    with self.assertRaises(LocalMutationError):transport.execute('ready','owner/repo',{'node':'node'})
                    self.assertEqual(service.graph.db.execute("SELECT state FROM local_mutation_attempts WHERE kind='ready'").fetchone(),('reserved',))
                    self.assertEqual(service.graph.db.execute('SELECT status FROM external_actions WHERE action=?',(ready_action,)).fetchone(),('uncertain',))
                else:self.assertEqual(transport.execute('ready','owner/repo',{'node':'node'}),{'id':'node','isDraft':False})
