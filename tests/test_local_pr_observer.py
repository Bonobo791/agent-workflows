import json,time,unittest
from tests.test_engineering_workspace import EngineeringWorkspaceTests
from tests.test_local_scope import Backend
from runner.local_scope import LocalScopes
from runner.local_pr_authority import LocalPRObserver
from runner.local_push import LocalPushTransport
from workflows.engineering.review_loop import BoundLedger

class ObserverTests(EngineeringWorkspaceTests):
    def test_draft_and_ready_observers_require_exact_receipt(self):
        from types import SimpleNamespace
        from runner.local_mutation import LocalMutationTransport
        from workflows.engineering.github import GitHub
        service=self.service;service.create('run-1','engineering','task');service.enqueue('run-1');queue=service.lifecycle.claim();claim=service.claim('run-1')
        head=self.base;target={'task':'task','repo':'owner/repo','branch':'task-1','base':'main','head':head,'diff':'c'*64}
        engine=Backend();service.model_supervisor=SimpleNamespace(backend=engine);LocalMutationTransport(service,'run-1',queue['token'],claim,None)
        scopes=LocalScopes(str(self.root/'state.db'),engine);self.addCleanup(scopes.close)
        ledger=BoundLedger(str(self.root/'state.db'),lambda run:dict(target),lambda run:True);self.addCleanup(ledger.db.close)
        github=GitHub(None,'owner','repo')
        class HTTP:
            draft=True;title='Title'
            def request(self,method,path,body=None):
                if '/pulls?state=' in path:return [{'number':7}]
                return {'number':7,'node_id':'node','state':'open','draft':self.draft,'title':self.title,'body':'Body','head':{'sha':head,'ref':'task-1','repo':{'full_name':'owner/repo'}},'base':{'ref':'main','repo':{'full_name':'owner/repo'}}}
        http=HTTP();observer=LocalPRObserver(service,http)
        for kind in ('draft','ready'):
            args={'title':'Title','body':'Body'} if kind=='draft' else {'number':7,'node':'node','digest':'a'*64,'dispositions':{}}
            action=github.draft_action('task-1','main','Title','Body') if kind=='draft' else github.ready_action(7,'node','a'*64)
            scope='github:owner/repo:branch:task-1' if kind=='draft' else 'github:owner/repo:pr:7'
            ledger.approve_bound('run-1',action,target,'owner',time.time()+60);ledger.reserve_scoped('run-1',action,head,scope)
            proposal=service.artifacts.put(json.dumps({'run':'run-1','kind':kind,'args':args,'target':target,'action':action}).encode());service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(proposal,'run-1',kind))
            assignment='job-'+('1' if kind=='draft' else '2')*48;service.graph.db.execute("INSERT INTO local_mutation_attempts VALUES(?,?,?,?,?,'reserved')",(assignment,'run-1',action,scope,kind))
            unit=scopes.reserve('run-1');service.graph.db.execute('INSERT INTO local_pr_scopes VALUES(?,?,?,?,?,?)',(unit,assignment,'run-1',action,scope,kind));engine.unit=unit;engine.state='active';scopes.bind('run-1',unit);scopes.stop('run-1',unit)
            http.draft=(kind=='draft');http.title='wrong';self.assertFalse(observer.observe('run-1',action,target,scope)['quiescent']) if kind=='draft' else None
            http.title='Title';result=observer.observe('run-1',action,target,scope);self.assertEqual(result['outcome'],'desired_state_observed')
            engine.empty=False;self.assertFalse(observer.observe('run-1',action,target,scope)['quiescent']);engine.empty=True
            ledger.resolve_scoped('run-1',action,head,scope,'executed')
    def test_remote_head_requires_exact_stopped_push_and_bundle_scopes(self):
        from types import SimpleNamespace
        service=self.service;service.create('run-1','engineering','task');service.enqueue('run-1');queue=service.lifecycle.claim();claim=service.claim('run-1')
        head=self.base;target={'task':'task','repo':'owner/repo','branch':'task-1','base':'main','head':head,'diff':'c'*64};action='push:'+'d'*64;scope='github:owner/repo:branch:task-1';assignment='job-'+'1'*48
        ledger=BoundLedger(str(self.root/'state.db'),lambda run:dict(target),lambda run:True);self.addCleanup(ledger.db.close)
        ledger.approve_bound('run-1',action,target,'owner',time.time()+60);ledger.reserve_scoped('run-1',action,head,scope)
        proposal=service.artifacts.put(json.dumps({'run':'run-1','kind':'push','args':{'expected_remote':None},'target':target,'action':action}).encode());service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(proposal,'run-1','push'))
        engine=Backend();service.model_supervisor=SimpleNamespace(backend=engine)
        LocalPushTransport(service,'run-1',queue['token'],claim,None)
        scopes=LocalScopes(str(self.root/'state.db'),engine);self.addCleanup(scopes.close)
        service.graph.db.execute("INSERT INTO local_push_attempts VALUES(?,?,?,?,?,'reserved')",(assignment,'run-1',action,scope,head))
        for kind in ('push','bundle'):
            unit=scopes.reserve('run-1');service.graph.db.execute('INSERT INTO local_pr_scopes VALUES(?,?,?,?,?,?)',(unit,assignment,'run-1',action,scope,kind));engine.unit=unit;engine.state='active';scopes.bind('run-1',unit);scopes.stop('run-1',unit)
            if kind=='bundle':service.graph.db.execute('INSERT INTO engineering_local_scopes VALUES(?,?,?)',('run-1',assignment,unit))
        class HTTP:
            head='bad'
            def request(self,method,path,body=None):return {'object':{'sha':self.head}}
        http=HTTP();observer=LocalPRObserver(service,http)
        self.assertFalse(observer.observe('run-1',action,target,scope)['quiescent'])
        http.head=head;self.assertEqual(observer.observe('run-1',action,target,scope),{'quiescent':True,'outcome':'desired_state_observed'})
        engine.empty=False;self.assertFalse(observer.observe('run-1',action,target,scope)['quiescent'])
