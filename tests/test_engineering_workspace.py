import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from runner.engineering_workspace import EngineeringWorkspace, WorkspaceError
from runner.worker import build_control

def git(path,*args):
    return subprocess.run(['git',*args],cwd=path,check=True,capture_output=True,text=True).stdout.strip()

class EngineeringWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); work=self.root/'workspaces'; work.mkdir(mode=0o700)
        parent=work/'run-1'; parent.mkdir(mode=0o700); self.repo=parent/'repo'; self.repo.mkdir(mode=0o700)
        git(self.repo,'init','-b','main'); (self.repo/'.git').chmod(0o700)
        git(self.repo,'config','user.name','Fixture'); git(self.repo,'config','user.email','fixture@example.invalid')
        (self.repo/'file').write_text('fixture'); git(self.repo,'add','file'); git(self.repo,'commit','-m','base')
        self.base=git(self.repo,'rev-parse','HEAD'); git(self.repo,'checkout','-b','task-1')
        git(self.repo,'remote','add','origin','https://github.com/owner/repo.git')
        from runner.manual_lifecycle import ManualLifecycle
        jobs=self.root/'jobs'; jobs.mkdir(mode=0o700)
        docker={'socket':'/tmp/fake-engine.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
        class Intake:
            config={'run':'run-1','workflow':'engineering','github_owner':'owner','github_repo':'repo','github_branch':'task-1'}
        self.service,self.control=build_control(str(self.root),'x'*40,docker_config=docker,intake_factory=lambda _:Intake(),lifecycle_factory=lambda _:ManualLifecycle(str(self.root/'state.db'),'worker','inc'))
        self.addCleanup(self.service.close); self.addCleanup(self.service.lifecycle.close)
        self.registry=EngineeringWorkspace(self.root); self.addCleanup(self.registry.close)
        self.policy=self.service.docker_supervisor.config.policy_hash; self.args=('run-1','owner/repo',self.base,'task-1',os.geteuid(),self.policy)
    def test_exact_binding_and_exclusive_replacement(self):
        with self.assertRaises(WorkspaceError): self.registry.register(*self.args,'0'*64)
        path=self.registry.register(*self.args,self.registry.binding_hash(*self.args)); a='job-'+'1'*48; b='job-'+'2'*48
        self.assertEqual(self.registry.claim('run-1',a,self.policy),path)
        with self.assertRaises(WorkspaceError): self.registry.claim('run-1',b,self.policy)
        with self.assertRaises(Exception): self.registry.release('run-1',a)
        db=self.registry.db
        db.execute('INSERT INTO docker_assignments VALUES(?,?,?,?)',(a,'id','terminated','fixture')); db.execute('INSERT INTO docker_terminal_evidence VALUES(?,?,?,?)',(a,'id','exited',1))
        self.registry.release('run-1',a); self.assertEqual(self.registry.claim('run-1',b,self.policy),path)
    def test_control_binding_and_fake_engine_replacement_mount(self):
        class Engine:
            def __init__(self): self.items={}; self.mounts=[]
            def request(self,method,path,body=None):
                if path=='/containers/create':
                    cid=format(len(self.items)+1,'064x'); self.items[cid]={'state':'created','label':body['Labels']['agent-workflows.run']}; self.mounts.append(body['HostConfig']['Mounts']); return 201,{'Id':cid}
                cid=path.split('/')[2]; item=self.items[cid]
                if path.endswith('/json'): return 200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run':item['label']}},'State':{'Running':item['state']=='running','Status':item['state']}}
                if path.endswith('/start'): item['state']='running'; return 204,None
                if '/stop?' in path: item['state']='exited'; return 204,None
                if '/wait?' in path: return 200,{'StatusCode':0}
                raise AssertionError(path)
        service=self.service; service.create('run-1','engineering','task'); service.enqueue('run-1')
        queue=self.control.handle('x'*40,{'action':'manual_claim'})['claim']; claim=self.control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
        service.approve('controller','docker_config',self.policy,'operator')
        request={'action':'engineering_workspace_binding','run':'run-1','queue_claim':queue['token'],'graph_claim':claim,'base':self.base}
        proposal=self.control.handle('x'*40,request); request.update(action='bind_engineering_workspace',binding_hash=proposal['binding_hash'])
        result=self.control.handle('x'*40,request)
        self.assertEqual(result['path'],str(self.repo))
        engine=Engine(); service.docker_assignments.engine=engine
        service.docker_supervisor.provider_ready=lambda:True
        first=service.docker_supervisor.start('run-1','implement'); self.assertTrue(first.finish())
        second=service.docker_supervisor.start('run-1','qa'); self.assertTrue(second.finish())
        self.assertNotEqual(first.assignment,second.assignment)
        self.assertEqual([next(m['Source'] for m in mounts if m['Target']=='/workspace') for mounts in engine.mounts],[str(self.repo),str(self.repo)])
    def test_rejects_wrong_identity_base_branch_owner_and_path(self):
        for args in [('run-1','other/repo',self.base,'task-1',os.geteuid(),self.policy),('run-1','owner/repo','0'*40,'task-1',os.geteuid(),self.policy),('run-1','owner/repo',self.base,'wrong',os.geteuid(),self.policy),('run-1','owner/repo',self.base,'task-1',os.geteuid()+1,self.policy),('../escape','owner/repo',self.base,'task-1',os.geteuid(),self.policy)]:
            with self.subTest(args=args), self.assertRaises(WorkspaceError): self.registry.register(*args,self.registry.binding_hash(*args))
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        git(self.repo,'checkout','main')
        with self.assertRaises(WorkspaceError): self.registry.claim('run-1','job-'+'3'*48,self.policy)
        git(self.repo,'checkout','task-1'); git(self.repo,'remote','set-url','origin','https://github.com/other/repo.git')
        with self.assertRaises(WorkspaceError): self.registry.claim('run-1','job-'+'4'*48,self.policy)
        git(self.repo,'remote','set-url','origin','https://github.com/owner/repo.git')
        with self.assertRaises(WorkspaceError): self.registry.claim('run-1','job-'+'5'*48,'b'*64)
        saved=self.repo.parent/'saved'; self.repo.rename(saved); self.repo.symlink_to(saved,target_is_directory=True)
        with self.assertRaises(WorkspaceError): self.registry.claim('run-1','job-'+'6'*48,self.policy)
