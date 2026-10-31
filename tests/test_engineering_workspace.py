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
    def test_local_claim_and_scope_reservation_are_atomic(self):
        import sqlite3
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        assignment='job-'+'a'*48;scopes=LocalScopes(str(self.root/'state.db'),Backend())
        try:
            self.registry.db.execute("CREATE TRIGGER reject_scope_binding BEFORE INSERT ON engineering_local_scopes BEGIN SELECT RAISE(ABORT,'unavailable'); END")
            with self.assertRaises(sqlite3.IntegrityError): self.registry.claim_local('run-1',assignment,self.policy)
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
            self.assertEqual(scopes.db.execute("SELECT COUNT(*) FROM local_scopes WHERE run='run-1'").fetchone(),(0,))
            self.registry.db.execute('DROP TRIGGER reject_scope_binding')
            path,unit=self.registry.claim_local('run-1',assignment,self.policy)
            self.assertEqual(path,str(self.repo))
            self.assertEqual(self.registry.db.execute('SELECT assignment FROM engineering_workspaces WHERE run=?',('run-1',)).fetchone(),(assignment,))
            self.assertEqual(self.registry.db.execute('SELECT run,unit FROM engineering_local_scopes WHERE assignment=?',(assignment,)).fetchone(),('run-1',unit))
            self.assertEqual(scopes.state('run-1',unit),'starting')
            from runner.audit_export import read_events
            events,cursor=read_events(self.registry.db,'workspace_scope_audit',0,10,b'x'*32)
            self.assertEqual((cursor,events[0]['status']),(1,'bound'))
            self.assertNotIn(unit,str(events));self.assertNotIn(assignment,str(events))
            scope_events,_=read_events(scopes.db,'local_scope_audit',0,10,b'x'*32)
            self.assertEqual(events[0]['scope_id'],scope_events[0]['scope_id'])
        finally: scopes.close()

    def test_push_scope_record_failure_rolls_back_reservation(self):
        import sqlite3
        from runner.local_push import LocalPushTransport
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        LocalPushTransport(self.service,'run-1','queue','graph',None)
        scopes=LocalScopes(str(self.root/'state.db'),Backend())
        try:
            self.registry.db.execute("CREATE TRIGGER reject_push_scope BEFORE INSERT ON local_pr_scopes BEGIN SELECT RAISE(ABORT,'unavailable'); END")
            with self.assertRaises(sqlite3.IntegrityError): self.registry.claim_local('run-1','job-'+'a'*48,self.policy,pr_scope=('push:x','github:o/r:branch:x','bundle'))
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
            for table in ('local_scopes','engineering_local_scopes','local_pr_scopes','workspace_scope_audit'):
                self.assertEqual(self.registry.db.execute('SELECT COUNT(*) FROM '+table).fetchone(),(0,))
        finally: scopes.close()

    def test_workspace_scope_binding_helper_requires_transaction(self):
        from runner.engineering_workspace import bind_scope_with_audit
        with self.assertRaises(WorkspaceError): bind_scope_with_audit(self.registry.db,'run-1','job-'+'a'*48,'scope')
        self.assertEqual(self.registry.db.execute('SELECT COUNT(*) FROM engineering_local_scopes').fetchone(),(0,))

    def test_missing_workspace_scope_audit_trigger_blocks_reservation(self):
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        scopes=LocalScopes(str(self.root/'state.db'),Backend())
        try:
            self.registry.db.execute('DROP TRIGGER workspace_scope_audit_insert')
            with self.assertRaises(WorkspaceError): self.registry.claim_local('run-1','job-'+'a'*48,self.policy)
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
            for table in ('local_scopes','engineering_local_scopes','workspace_scope_audit'):
                self.assertEqual(self.registry.db.execute('SELECT COUNT(*) FROM '+table).fetchone(),(0,))
        finally: scopes.close()

    def test_workspace_scope_audit_failure_rolls_back_reservation(self):
        import sqlite3
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        scopes=LocalScopes(str(self.root/'state.db'),Backend())
        try:
            self.registry.db.execute("CREATE TRIGGER reject_workspace_scope_audit BEFORE INSERT ON workspace_scope_audit BEGIN SELECT RAISE(ABORT,'unavailable'); END")
            with self.assertRaises(sqlite3.IntegrityError): self.registry.claim_local('run-1','job-'+'a'*48,self.policy)
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
            for table in ('local_scopes','engineering_local_scopes','local_scope_audit','workspace_scope_audit'):
                self.assertEqual(self.registry.db.execute('SELECT COUNT(*) FROM '+table).fetchone(),(0,))
        finally: scopes.close()

    def test_concurrent_local_claims_serialize_on_shared_database(self):
        import concurrent.futures
        import threading
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        scopes=LocalScopes(str(self.root/'state.db'),Backend());scopes.close()
        gate=threading.Barrier(2)
        def claim(assignment):
            registry=EngineeringWorkspace(self.root)
            try:
                gate.wait(timeout=5)
                try: return registry.claim_local('run-1',assignment,self.policy)
                except WorkspaceError: return None
            finally: registry.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(claim,'job-'+digit*48) for digit in ('a','b')]
            results=[future.result(timeout=15) for future in futures]
        self.assertEqual(sum(result is not None for result in results),1)
        for table in ('local_scopes','engineering_local_scopes','workspace_scope_audit'):
            self.assertEqual(self.registry.db.execute('SELECT COUNT(*) FROM '+table).fetchone(),(1,))

    def test_local_claim_rejects_changed_expected_workspace_without_reservation(self):
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        scopes=LocalScopes(str(self.root/'state.db'),Backend())
        try:
            with self.assertRaises(WorkspaceError): self.registry.claim_local('run-1','job-'+'a'*48,self.policy,expected_path=str(self.root/'other'))
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
            self.assertEqual(scopes.db.execute('SELECT COUNT(*) FROM local_scopes').fetchone(),(0,))
        finally: scopes.close()

    def test_interrupted_local_launch_preserves_exact_workspace_scope_binding(self):
        from unittest.mock import patch
        from runner.local_process import LocalProcessError
        from runner.local_supervisor import LocalSupervisor
        from tests.test_local_scope import Backend
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        binary=self.root/'codex';binary.write_text('synthetic');binary.chmod(0o700)
        supervisor=LocalSupervisor(str(self.root/'state.db'),str(binary),{'HOME':str(self.root)},Backend(),lambda _:True,lambda *_:str(self.repo),lambda:True,{'mcp_servers':[],'plugins':[]},engineering_root=str(self.root),policy_hash=self.policy)
        with patch.object(supervisor,'preflight',return_value=True),patch.object(EngineeringWorkspace,'verify',return_value=str(self.repo)),patch('runner.local_process.subprocess.Popen',side_effect=OSError('launch interrupted')):
            with self.assertRaises(LocalProcessError): supervisor.start_execution('run-1','implement',None,{},'model')
        row=self.registry.db.execute("SELECT w.assignment,m.unit,s.state FROM engineering_workspaces w LEFT JOIN engineering_local_scopes m ON m.run=w.run AND m.assignment=w.assignment LEFT JOIN local_scopes s ON s.run=m.run AND s.unit=m.unit WHERE w.run='run-1'").fetchone()
        self.assertIsNotNone(row[0]);self.assertIsNotNone(row[1]);self.assertEqual(row[2],'starting')

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
            identity=scopes.db.execute('SELECT invocation,cgroup FROM local_scopes WHERE unit=?',(unit,)).fetchone()
            for field,bad,value in (('invocation',None,identity[0]),('invocation','bad',identity[0]),('cgroup',None,identity[1]),('cgroup','/wrong',identity[1])):
                scopes.db.execute('UPDATE local_scopes SET '+field+'=? WHERE unit=?',(bad,unit))
                with self.subTest(field=field,bad=bad),self.assertRaises(WorkspaceError): self.registry.release_local('run-1',assignment,unit)
                self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(assignment,))
                scopes.db.execute('UPDATE local_scopes SET '+field+'=? WHERE unit=?',(value,unit))
            with self.assertRaises(WorkspaceError): self.registry.release_local('run-1',assignment,'wrong.scope')
            self.registry.release_local('run-1',assignment,unit)
            self.assertEqual(self.registry.db.execute('SELECT assignment FROM engineering_workspaces WHERE run=?',('run-1',)).fetchone(),(None,))
        finally:scopes.close()
    def test_fenced_control_releases_only_exact_terminated_local_workspace(self):
        from types import SimpleNamespace
        from unittest.mock import patch
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
            original=EngineeringWorkspace.release_local
            def fence_changes(registry,*args,**kwargs):
                self.registry.db.execute("UPDATE jobs SET status='cancelled' WHERE run='run-1'")
                return original(registry,*args,**kwargs)
            with patch.object(EngineeringWorkspace,'release_local',fence_changes):
                with self.assertRaises(ControlError): self.control.handle('x'*40,request)
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(assignment,))
            self.registry.db.execute("UPDATE jobs SET status='cancelling' WHERE run='run-1'")
            for field,value in (('assignment','job-'+'b'*48),('unit','other.scope')):
                with self.subTest(field=field),self.assertRaises(ControlError): self.control.handle('x'*40,{**request,field:value})
            self.registry.db.execute('DELETE FROM engineering_local_scopes WHERE assignment=?',(assignment,))
            with self.assertRaises(ControlError): self.control.handle('x'*40,request)
            self.registry.db.execute('INSERT INTO engineering_local_scopes VALUES(?,?,?)',('run-1',assignment,unit))
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(assignment,))
            self.registry.db.execute("CREATE TRIGGER reject_workspace_recovery_audit BEFORE INSERT ON workspace_recovery_audit BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
            with self.assertRaises(ControlError): self.control.handle('x'*40,request)
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(assignment,))
            self.registry.db.execute('DROP TRIGGER reject_workspace_recovery_audit')
            for table,key,rejected,restored in (('graph_runs','id','active','cancelled'),('jobs','run','running','cancelling')):
                with self.subTest(table=table):
                    service.graph.db.execute('UPDATE '+table+' SET status=? WHERE '+key+'=?',(rejected,'run-1'))
                    try:
                        with self.assertRaises(ControlError): self.control.handle('x'*40,request)
                        self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(assignment,))
                    finally: service.graph.db.execute('UPDATE '+table+' SET status=? WHERE '+key+'=?',(restored,'run-1'))
            self.assertEqual(self.control.handle('x'*40,request),{'released':True})
            self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
            from runner.audit_export import read_events
            events,cursor=read_events(self.registry.db,'workspace_recovery_audit',0,10,b'x'*32)
            scopes_audit,_=read_events(self.registry.db,'local_scope_audit',0,10,b'x'*32)
            self.assertEqual(cursor,1);self.assertEqual(events[0]['status'],'released')
            self.assertEqual(events[0]['scope_id'],scopes_audit[-1]['scope_id'])
            from runner.otel_audit import _attributes
            self.assertEqual(_attributes(events[0])['scope_id'],events[0]['scope_id'])
            self.assertIsNotNone(events[0]['execution_id'])
            self.assertNotIn(assignment,str(events));self.assertNotIn(unit,str(events))
            with self.assertRaises(ControlError): self.control.handle('x'*40,request)
        finally: scopes.close()

    def test_recover_terminated_workspace_without_local_supervisor(self):
        from types import SimpleNamespace
        from runner.control import ControlError, ControlPlane
        from runner.local_scope import LocalScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.service import Service
        from tests.test_local_scope import Backend
        service=self.service;service.create('run-1','engineering','task');service.enqueue('run-1')
        self.control.handle('x'*40,{'action':'manual_claim'})
        self.registry.register(*self.args,self.registry.binding_hash(*self.args));assignment='job-'+'d'*48
        self.registry.claim('run-1',assignment,self.policy)
        backend=Backend();service.model_supervisor=SimpleNamespace(backend=backend,engineering_root=str(self.root))
        scopes=LocalScopes(str(self.root/'state.db'),backend)
        try:
            unit=scopes.reserve('run-1');backend.unit=unit;scopes.bind('run-1',unit)
            self.registry.bind_local_scope('run-1',assignment,unit)
            self.control.handle('x'*40,{'action':'manual_stop','run':'run-1'})
            restarted=Service(self.root);lifecycle=ManualLifecycle(str(self.root/'state.db'),'worker','inc');restarted.lifecycle=lifecycle
            try:
                control=ControlPlane(restarted,'x'*40)
                stop=control.handle('x'*40,{'action':'manual_stop','run':'run-1'})
                self.assertEqual(stop['local_workspace'],{'assignment':assignment,'unit':unit})
                with self.assertRaises(ControlError): control.handle('x'*40,{'action':'stop_local_scope','run':'run-1','unit':unit})
                release={'action':'release_local_workspace','run':'run-1','assignment':assignment,'unit':unit}
                with self.assertRaises(ControlError): control.handle('x'*40,release)
                self.assertTrue(scopes.stop('run-1',unit))
                foreign=ManualLifecycle(str(self.root/'other.db'),'worker','inc');restarted.lifecycle=foreign
                try:
                    with self.assertRaises(ControlError): control.handle('x'*40,release)
                finally: restarted.lifecycle=lifecycle;foreign.close()
                self.assertEqual(control.handle('x'*40,release),{'released':True})
                self.assertEqual(control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run-1','incarnation':'inc','attestation':'terminated_and_fenced'})['status'],'stopped')
            finally:restarted.close();lifecycle.close()
        finally:scopes.close()

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

    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_fenced_workspace_recovery_after_claiming_process_crash(self):
        import sys,time
        from types import SimpleNamespace
        from runner.control import ControlError
        from runner.local_scope import LocalScopes, SystemdScopes
        code='''
import os,subprocess,sys,time
from pathlib import Path
from runner.engineering_workspace import EngineeringWorkspace
from runner.local_scope import LocalScopes,SystemdScopes,ScopeError
root,policy,assignment,ready=sys.argv[1:]
registry=EngineeringWorkspace(root)
backend=SystemdScopes(os.environ);scopes=LocalScopes(str(Path(root)/'state.db'),backend)
_,unit=registry.claim_local('run-1',assignment,policy)
launcher=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--','/usr/bin/sleep','120'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
deadline=time.monotonic()+10
while True:
    try:
        if backend.inspect(unit)['active']=='active': break
    except ScopeError: pass
    if launcher.poll() is not None or time.monotonic()>=deadline: raise RuntimeError('scope did not start')
    time.sleep(.05)
Path(ready).write_text(unit)
time.sleep(60)
'''
        service=self.service;service.create('run-1','engineering','task');service.enqueue('run-1')
        self.control.handle('x'*40,{'action':'manual_claim'})
        self.registry.register(*self.args,self.registry.binding_hash(*self.args));assignment='job-'+'c'*48
        backend=SystemdScopes(os.environ);service.model_supervisor=SimpleNamespace(backend=backend,engineering_root=str(self.root))
        scopes=LocalScopes(str(self.root/'state.db'),backend);ready=self.root/'claim-ready'
        with tempfile.TemporaryFile() as errors:
            worker=subprocess.Popen([sys.executable,'-c',code,str(self.root),self.policy,assignment,str(ready)],stdout=subprocess.DEVNULL,stderr=errors)
            try:
                deadline=time.monotonic()+15
                while not ready.exists() and worker.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                if not ready.exists():
                    errors.seek(0);self.fail(f'claiming process did not start (exit={worker.poll()}): {errors.read(2000).decode(errors="replace")}')
                unit=ready.read_text();group=backend.inspect(unit)['cgroup']
                worker.kill();self.assertEqual(worker.wait(timeout=5),-9)
                self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(assignment,))
                self.assertEqual(scopes.state('run-1',unit),'starting')
                stop=self.control.handle('x'*40,{'action':'manual_stop','run':'run-1'})
                self.assertEqual(stop['local_workspace'],{'assignment':assignment,'unit':unit})
                release={'action':'release_local_workspace','run':'run-1','assignment':assignment,'unit':unit}
                with self.assertRaises(ControlError):self.control.handle('x'*40,release)
                self.control.handle('x'*40,{'action':'stop_local_scope','run':'run-1','unit':unit})
                self.assertEqual(backend.inspect(unit)['active'],'inactive');self.assertTrue(backend.empty_cgroup(group))
                confirm={'action':'manual_confirm_stopped','run':'run-1','incarnation':'inc','attestation':'terminated_and_fenced'}
                with self.assertRaises(ControlError):self.control.handle('x'*40,confirm)
                self.assertEqual(self.control.handle('x'*40,release),{'released':True})
                self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
                self.assertEqual(self.control.handle('x'*40,confirm)['status'],'stopped')
            finally:
                if worker.poll() is None:worker.kill();worker.wait(timeout=5)
                rows=scopes.db.execute("SELECT unit FROM local_scopes WHERE run='run-1'").fetchall()
                for (unit,) in rows:
                    subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
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
            self.assertEqual(workspace,str(self.repo));self.assertTrue(kwargs['allow_git'])
            root,assignment,policy=kwargs['workspace_claim']
            self.assertEqual((root,policy),(str(self.root),supervisor.policy_hash))
            _,unit=self.registry.claim_local(run,assignment,policy)
            scopes=LocalScopes(db,backend)
            try:backend.unit=unit;scopes.bind(run,unit)
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
