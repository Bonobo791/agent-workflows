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
    def test_local_claim_requires_exact_terminal_scope(self):
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        self.registry.register(*self.args,self.registry.binding_hash(*self.args)); assignment='job-'+'a'*48
        self.registry.claim('run-1',assignment,self.policy)
        backend=Backend();scopes=LocalScopes(str(self.root/'state.db'),backend)
        try:
            unit=scopes.reserve('run-1');backend.unit=unit;scopes.bind('run-1',unit)
            self.registry.bind_local_scope('run-1',assignment,unit)
            with self.assertRaises(WorkspaceError): self.registry.release_local('run-1',assignment,unit)
            self.assertTrue(scopes.stop('run-1',unit))
            with self.assertRaises(WorkspaceError): self.registry.release_local('run-1',assignment,'wrong.scope')
            self.registry.release_local('run-1',assignment,unit)
            self.assertEqual(self.registry.db.execute('SELECT assignment FROM engineering_workspaces WHERE run=?',('run-1',)).fetchone(),(None,))
        finally:scopes.close()
    def test_fenced_control_releases_only_exact_terminated_local_workspace(self):
        from types import SimpleNamespace
        from runner.control import ControlError
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        service=self.service;service.create('run-1','engineering','task');service.enqueue('run-1')
        self.control.handle('x'*40,{'action':'manual_claim'})
        self.registry.register(*self.args,self.registry.binding_hash(*self.args));assignment='job-'+'a'*48
        self.registry.claim('run-1',assignment,self.policy)
        backend=Backend();service.model_supervisor=SimpleNamespace(backend=backend,engineering_root=str(self.root))
        scopes=LocalScopes(str(self.root/'state.db'),backend)
        try:
            unit=scopes.reserve('run-1');backend.unit=unit;scopes.bind('run-1',unit)
            self.registry.bind_local_scope('run-1',assignment,unit)
            request={'action':'release_local_workspace','run':'run-1','assignment':assignment,'unit':unit}
            with self.assertRaises(ControlError): self.control.handle('wrong',request)
            with self.assertRaises(ControlError): self.control.handle('x'*40,request)
            stop=self.control.handle('x'*40,{'action':'manual_stop','run':'run-1'})
            self.assertEqual(stop['local_workspace'],{'assignment':assignment,'unit':unit})
            with self.assertRaises(ControlError): self.control.handle('x'*40,request)
            self.assertTrue(scopes.stop('run-1',unit))
            for field,value in (('assignment','job-'+'b'*48),('unit','other.scope')):
                with self.subTest(field=field),self.assertRaises(ControlError): self.control.handle('x'*40,{**request,field:value})
            service.model_supervisor.engineering_root=str(self.root/'other')
            with self.assertRaises(ControlError): self.control.handle('x'*40,request)
            service.model_supervisor.engineering_root=str(self.root)
            self.registry.db.execute('DELETE FROM engineering_local_scopes WHERE assignment=?',(assignment,))
            with self.assertRaises(ControlError): self.control.handle('x'*40,request)
            self.registry.db.execute('INSERT INTO engineering_local_scopes VALUES(?,?,?)',('run-1',assignment,unit))
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(assignment,))
            self.assertEqual(self.control.handle('x'*40,request),{'released':True})
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
            with self.assertRaises(ControlError): self.control.handle('x'*40,request)
        finally: scopes.close()

    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_fenced_workspace_recovery_with_real_scope(self):
        import time
        from types import SimpleNamespace
        from runner.control import ControlError
        from runner.local_scope import LocalScopes, SystemdScopes, ScopeError
        service=self.service;service.create('run-1','engineering','task');service.enqueue('run-1')
        self.control.handle('x'*40,{'action':'manual_claim'})
        self.registry.register(*self.args,self.registry.binding_hash(*self.args));assignment='job-'+'a'*48
        self.registry.claim('run-1',assignment,self.policy)
        backend=SystemdScopes(os.environ);service.model_supervisor=SimpleNamespace(backend=backend,engineering_root=str(self.root))
        scopes=LocalScopes(str(self.root/'state.db'),backend);unit=scopes.reserve('run-1')
        launcher=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--','/usr/bin/sleep','120'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+10
            while True:
                try: scopes.bind('run-1',unit);break
                except ScopeError:
                    if launcher.poll() is not None or time.monotonic()>=deadline: raise
                    time.sleep(.05)
            group=backend.inspect(unit)['cgroup'];self.registry.bind_local_scope('run-1',assignment,unit)
            stop=self.control.handle('x'*40,{'action':'manual_stop','run':'run-1'})
            self.assertEqual(stop['local_workspace'],{'assignment':assignment,'unit':unit})
            release={'action':'release_local_workspace','run':'run-1','assignment':assignment,'unit':unit}
            with self.assertRaises(ControlError): self.control.handle('x'*40,release)
            self.assertEqual(self.control.handle('x'*40,{'action':'stop_local_scope','run':'run-1','unit':unit}),{'unit':unit,'state':'terminated'})
            self.assertEqual(backend.inspect(unit)['active'],'inactive');self.assertTrue(backend.empty_cgroup(group))
            confirm={'action':'manual_confirm_stopped','run':'run-1','incarnation':'inc','attestation':'terminated_and_fenced'}
            with self.assertRaises(ControlError): self.control.handle('x'*40,confirm)
            self.assertEqual(self.control.handle('x'*40,release),{'released':True})
            self.assertEqual(self.control.handle('x'*40,confirm)['status'],'stopped')
        finally:
            subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
            try: launcher.wait(timeout=5)
            except subprocess.TimeoutExpired: launcher.kill();launcher.wait(timeout=5)
            scopes.close()

    def test_local_engineering_binding_claims_exact_checkout_until_scope_stops(self):
        import hashlib
        from unittest.mock import patch
        from runner.local_config import local_supervisor_factory
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        binary=self.root/'codex';binary.write_bytes(b'synthetic');binary.chmod(0o700)
        config={'codex_executable':str(binary),'codex_sha256':hashlib.sha256(b'synthetic').hexdigest(),'disabled_integrations':{'mcp_servers':[],'plugins':[]}}
        service=self.service;supervisor=local_supervisor_factory(config,{'HOME':str(self.root)},str(self.root))(service)
        service.model_supervisor=supervisor;supervisor.fence=lambda _:True;backend=Backend();supervisor.backend=backend
        service.create('run-1','engineering','task');service.enqueue('run-1')
        queue=self.control.handle('x'*40,{'action':'manual_claim'})['claim'];claim=self.control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
        request={'action':'engineering_workspace_binding','run':'run-1','queue_claim':queue['token'],'graph_claim':claim,'base':self.base}
        proposal=self.control.handle('x'*40,request);request.update(action='bind_engineering_workspace',binding_hash=proposal['binding_hash'])
        self.assertEqual(self.control.handle('x'*40,request)['path'],str(self.repo))
        class Handle:
            output_dir=str(self.repo);output_path_prefix=str(self.repo)
            def __init__(handle,unit):handle.unit=unit
            def finish(handle):
                scopes=LocalScopes(str(self.root/'state.db'),backend)
                try:return scopes.stop('run-1',handle.unit)
                finally:scopes.close()
            def cancel(handle):return handle.finish()
        def start(db,run,workspace,executable,environment,backend_arg,fence,integrations,**kwargs):
            self.assertEqual(workspace,str(self.repo));self.assertEqual(kwargs,{'allow_git':True})
            scopes=LocalScopes(db,backend)
            try:unit=scopes.reserve(run);backend.unit=unit;scopes.bind(run,unit)
            finally:scopes.close()
            return Handle(unit)
        with patch('runner.local_supervisor.sandbox_preflight',return_value=True),patch('runner.local_supervisor.scope_preflight',return_value=True),patch('runner.local_supervisor.start_local_process',side_effect=start):
            handle=supervisor.start_execution('run-1','implement',None,{},'model')
            self.assertIsNotNone(self.registry.db.execute('SELECT assignment FROM engineering_workspaces WHERE run=?',('run-1',)).fetchone()[0])
            handle.bind_session('thread-1');self.assertTrue(handle.finish())
            self.assertEqual(self.registry.db.execute('SELECT assignment FROM engineering_workspaces WHERE run=?',('run-1',)).fetchone(),(None,))
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
