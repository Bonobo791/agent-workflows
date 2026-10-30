import json
import time
import unittest
from tests.test_engineering_workspace import EngineeringWorkspaceTests
from runner.docker_push import DockerPushTransport
from runner.pr_observer import PRRemoteObserver
from workflows.engineering.review_loop import BoundLedger

class PRObserverTests(EngineeringWorkspaceTests):
    def test_draft_receipt_requires_exact_pr_identity_and_signed_quiescence(self):
        service=self.service; service.create('run-1','engineering','task'); service.enqueue('run-1')
        queue=service.lifecycle.claim(); claim=service.claim('run-1'); head=self.base
        target={'task':'task','repo':'owner/repo','branch':'task-1','base':'main','head':head,'diff':'c'*64}
        action='open_draft:'+'e'*64; scope='github:owner/repo:branch:task-1'; assignment='job-'+'2'*48; cid='b'*64
        ledger=BoundLedger(str(self.root/'state.db'),lambda run:dict(target),lambda run:service.queue.may_start(run,queue['token'])); self.addCleanup(ledger.db.close)
        ledger.approve_bound('run-1',action,target,'owner',time.time()+60); ledger.reserve_scoped('run-1',action,head,scope)
        proposal=service.artifacts.put(json.dumps({'run':'run-1','kind':'draft','args':{'title':'T','body':'B'},'target':target,'action':action}).encode())
        service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(proposal,'run-1','draft'))
        class Credentials:
            def ready(self): return False
        DockerPushTransport(service,'run-1',queue['token'],claim,Credentials())
        db=service.graph.db
        db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',('run-1','pr_mutation',assignment))
        db.execute('INSERT INTO docker_assignments VALUES(?,?,?,?)',(assignment,cid,'running',service.docker_supervisor.config.image))
        db.execute('INSERT INTO docker_disposable_assignments VALUES(?,?,?)',(assignment,cid,self.policy))
        db.execute('INSERT INTO docker_assignment_policy VALUES(?,?)',(assignment,self.policy))
        db.execute('INSERT INTO pr_mutation_processes VALUES(?,?,?,?,?)',(assignment,'run-1',action,scope,'draft'))
        class Engine:
            def request(self,method,path,body=None): return 200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run':assignment}},'State':{'Running':False,'Status':'exited','ExitCode':0},'HostConfig':{'RestartPolicy':{'Name':'no'},'AutoRemove':False}}
        class HTTP:
            branch='task-1'
            def request(self,method,path,body=None):
                if '/pulls?state=' in path: return [{'number':7}]
                return {'number':7,'state':'open','draft':True,'title':'T','body':'B','head':{'sha':head,'ref':self.branch,'repo':{'full_name':'owner/repo'}},'base':{'ref':'main','repo':{'full_name':'owner/repo'}}}
        service.docker_assignments.engine=Engine(); http=HTTP(); observer=PRRemoteObserver(service,http)
        result=observer.observe('run-1',action,target,scope); self.assertEqual(result['remote_receipt']['number'],7)
        http.branch='other'; self.assertFalse(observer.observe('run-1',action,target,scope)['quiescent'])

    def test_process_and_independent_quiescence_required_before_remote_outcome(self):
        service=self.service; service.create('run-1','engineering','task'); service.enqueue('run-1')
        queue=service.lifecycle.claim(); claim=service.claim('run-1'); head=self.base
        target={'task':'task','repo':'owner/repo','branch':'task-1','base':'main','head':head,'diff':'c'*64}
        action='push:'+'d'*64; scope='github:owner/repo:branch:task-1'; assignment='job-'+'1'*48; cid='b'*64
        lock=lambda run:service.queue.may_start(run,queue['token'])
        ledger=BoundLedger(str(self.root/'state.db'),lambda run:dict(target),lock)
        self.addCleanup(ledger.db.close)
        ledger.approve_bound('run-1',action,target,'owner',time.time()+60); ledger.reserve_scoped('run-1',action,head,scope)
        proposal=service.artifacts.put(json.dumps({'run':'run-1','kind':'push','args':{'expected_remote':'a'*40},'target':target,'action':action}).encode())
        service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(proposal,'run-1','push'))
        class Credentials:
            def ready(self): return False
        DockerPushTransport(service,'run-1',queue['token'],claim,Credentials())
        db=service.graph.db
        db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',('run-1','push',assignment))
        db.execute('INSERT INTO docker_assignments VALUES(?,?,?,?)',(assignment,cid,'running',service.docker_supervisor.config.image))
        db.execute('INSERT INTO docker_disposable_assignments VALUES(?,?,?)',(assignment,cid,self.policy))
        db.execute('INSERT INTO docker_assignment_policy VALUES(?,?)',(assignment,self.policy))
        db.execute('INSERT INTO pr_mutation_processes VALUES(?,?,?,?,?)',(assignment,'run-1',action,scope,'push'))
        class Engine:
            running=False; label=assignment
            def request(self,method,path,body=None):
                return 200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run':self.label}},'State':{'Running':self.running,'Status':'running' if self.running else 'exited','ExitCode':0},'HostConfig':{'RestartPolicy':{'Name':'no'},'AutoRemove':False}}
        class HTTP:
            head=target['head']
            def request(self,method,path,body=None): return {'object':{'sha':self.head}}
        engine=Engine(); service.docker_assignments.engine=engine; http=HTTP(); observer=PRRemoteObserver(service,http)
        engine.running=True; self.assertFalse(observer.observe('run-1',action,target,scope)['quiescent'])
        engine.running=False; engine.label='wrong'; self.assertFalse(observer.observe('run-1',action,target,scope)['quiescent'])
        engine.label=assignment; http.head='f'*40; self.assertFalse(observer.observe('run-1',action,target,scope)['quiescent'])
        http.head='a'*40; self.assertFalse(observer.observe('run-1',action,target,scope)['quiescent'])
        http.head=head; self.assertEqual(observer.observe('run-1',action,target,scope),{'quiescent':True,'outcome':'desired_state_observed'})
