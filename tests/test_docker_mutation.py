import hashlib
import json
import socket
import struct
import threading
import time
from runner.docker_config import DockerConfig
from runner.docker_mutation import DockerMutationTransport
from runner.docker_controller import DockerError
from runner.engineering_dispatch import workspace_snapshot
from runner.pr_mutation_helper import execute
from tests.test_engineering_workspace import EngineeringWorkspaceTests
from workflows.engineering.github import GitHub
from workflows.engineering.review_loop import BoundLedger

class MutationTests(EngineeringWorkspaceTests):
    def test_fixed_helper_returns_only_selected_fields(self):
        class HTTP:
            def __init__(self,token): self.token=token
            def request(self,method,path,body): return {'number':7,'draft':True,'head':{'sha':'a'*40},'untrusted_echo':self.token}
            def mark_ready(self,node): return {'id':node,'isDraft':False,'untrusted_echo':self.token}
        msg={'credential':'fixture-secret-marker','repo':'owner/repo','kind':'draft','payload':{'head':'task-1','base':'main','title':'Title','body':'Body','draft':True}}
        result=execute(msg,HTTP); self.assertNotIn('fixture-secret-marker',json.dumps(result)); self.assertEqual(result['number'],7)
        msg.update(kind='ready',payload={'node':'node'}); self.assertNotIn('fixture-secret-marker',json.dumps(execute(msg,HTTP)))
        with self.assertRaises(ValueError): execute({**msg,'kind':'other'},HTTP)
    def test_exact_container_private_channel_for_draft_and_ready(self):
        self.service.create('run-1','engineering','task'); self.service.enqueue('run-1')
        queue=self.service.lifecycle.claim(); claim=self.service.claim('run-1'); service=self.service
        old=service.docker_supervisor.config; cfg=DockerConfig({'socket':old.socket,'image':old.image,'network':'aw-fixture','job_root':str(old.job_root)},str(self.root)); service.docker_supervisor.config=cfg
        service.approve('controller','docker_config',cfg.policy_hash,'operator')
        args=('run-1','owner/repo',self.base,'task-1',__import__('os').geteuid(),cfg.policy_hash); self.registry.register(*args,self.registry.binding_hash(*args))
        workspace=workspace_snapshot(service,'run-1'); target={'task':'task','repo':'owner/repo','branch':'task-1','base':'main','head':workspace['head'],'diff':hashlib.sha256(workspace['diff'].encode()).hexdigest()}
        class Credentials:
            received=[]; invalid=False; mismatch=False
            def ready(self): return True
            def issue(self,run,action,assignment,cid): self.received.append((run,action,assignment,cid)); return None if self.invalid else {'run':run,'action':action,'assignment':assignment,'container_id':'wrong' if self.mismatch else cid,'expires':time.time()+60,'credential':'fixture-secret-marker'}
        class Engine:
            def __init__(self): self.jobs={}; self.counter=0; self.messages=[]; self.specs=[]; self.mismatch=False
            def request(self,method,path,body=None):
                if path=='/containers/create':
                    self.counter+=1; cid=format(self.counter,'064x'); self.jobs[cid]={'spec':body,'state':'created'}; self.specs.append(body); return 201,{'Id':cid}
                cid=path.split('/')[2]; job=self.jobs[cid]
                if path.endswith('/json'): return 200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run': 'wrong' if self.mismatch else job['spec']['Labels']['agent-workflows.run']}},'State':{'Running':job['state']=='running','Status':job['state'],'ExitCode':0}}
                if path.endswith('/start'): job['state']='running'; return 204,None
                if '/wait?' in path: job['state']='exited'; return 200,{'StatusCode':0}
                raise AssertionError(path)
            def attach(self,cid):
                client,server=socket.socketpair()
                def worker():
                    try:
                        data=b''
                        while not data.endswith(b'\n'):
                            part=server.recv(65536)
                            if not part: return
                            data+=part
                        message=json.loads(data); self.messages.append(message)
                        result={'number':7,'draft':True,'head':{'sha':target['head']}} if message['kind']=='draft' else {'id':'node','isDraft':False}
                        encoded=json.dumps({'ok':True,'result':result}).encode()+b'\n'; server.sendall(struct.pack('>BxxxI',1,len(encoded))+encoded)
                    except OSError: pass
                    finally: server.close()
                thread=threading.Thread(target=worker); thread.start(); self.add_thread=thread
                return client
        engine=Engine(); service.docker_assignments.engine=engine; creds=Credentials(); transport=DockerMutationTransport(service,'run-1',queue['token'],claim,creds)
        ledger=BoundLedger(str(self.root/'state.db'),lambda run:dict(target),lambda run:service.queue.may_start(run,queue['token'])); self.addCleanup(ledger.db.close)
        github=GitHub(None,'owner','repo')
        from runner.pr_authority import ReadAndIsolatedPush
        from types import SimpleNamespace
        service.task_intake=SimpleNamespace(config={'github_owner':'owner','github_repo':'repo','github_branch':'task-1'})
        adapter=ReadAndIsolatedPush(None,None,transport)
        for kind in ('draft','ready'):
            if kind=='draft':
                args={'title':'Title','body':'Body'}; payload={'head':'task-1','base':'main','title':'Title','body':'Body','draft':True}; action=github.draft_action('task-1','main','Title','Body'); scope='github:owner/repo:branch:task-1'
            else:
                args={'number':7,'node':'node','digest':'d'*64,'dispositions':{}}; payload={'node':'node'}; action=github.ready_action(7,'node','d'*64); scope='github:owner/repo:pr:7'
            proposal=service.artifacts.put(json.dumps({'run':'run-1','kind':kind,'args':args,'target':target,'action':action}).encode())
            service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(proposal,'run-1',kind))
            ledger.approve_bound('run-1',action,target,'owner',time.time()+60); ledger.reserve_scoped('run-1',action,target['head'],scope)
            result=adapter.request('POST','/repos/owner/repo/pulls',payload) if kind=='draft' else adapter.mark_ready('node')
            engine.add_thread.join(); self.assertTrue(result)
            ledger.resolve_scoped('run-1',action,target['head'],scope,'executed')
        self.assertEqual(len(creds.received),2)
        for item in engine.specs:
            self.assertEqual(item['Entrypoint'],['python','-m','runner.pr_mutation_helper']); self.assertEqual(item['HostConfig']['Mounts'],[])
            self.assertNotIn('fixture-secret-marker',json.dumps(item))
        self.assertEqual([m['kind'] for m in engine.messages],['draft','ready'])
        self.assertNotIn('fixture-secret-marker',''.join(service.graph.db.iterdump()))
        action=github.draft_action('task-1','main','Different','Body'); scope='github:owner/repo:branch:task-1'
        proposal=service.artifacts.put(json.dumps({'run':'run-1','kind':'draft','args':{'title':'Different','body':'Body'},'target':target,'action':action}).encode())
        service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(proposal,'run-1','draft'))
        ledger.approve_bound('run-1',action,target,'owner',time.time()+60); ledger.reserve_scoped('run-1',action,target['head'],scope)
        if getattr(self,'timeout_case',False):
            from unittest.mock import patch
            with patch('runner.docker_mutation.DockerAttachTransport.receive',side_effect=TimeoutError):
                with self.assertRaises(DockerError): transport.execute('draft','owner/repo',{'head':'task-1','base':'main','title':'Different','body':'Body','draft':True})
            engine.add_thread.join(); self.assertEqual(len(engine.messages),3)
        else:
            creds.mismatch=getattr(self,'mismatch_case',False); creds.invalid=not creds.mismatch
            with self.assertRaises(DockerError): transport.execute('draft','owner/repo',{'head':'task-1','base':'main','title':'Different','body':'Body','draft':True})
            engine.add_thread.join(); self.assertEqual(len(engine.messages),2)
        self.assertEqual(ledger.db.execute('SELECT status FROM external_actions WHERE run=? AND action=?',('run-1',action)).fetchone(),('uncertain',))

    def test_timeout_remains_fenced(self):
        self.timeout_case=True
        self.test_exact_container_private_channel_for_draft_and_ready()

    def test_wrong_container_credential_is_rejected(self):
        self.mismatch_case=True
        self.test_exact_container_private_channel_for_draft_and_ready()
