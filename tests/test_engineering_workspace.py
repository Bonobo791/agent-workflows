import os
import subprocess
import tempfile
import unittest
import time
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
        binary=self.root/'codex'; binary.write_text('synthetic'); binary.chmod(0o700)
        from runner.local_supervisor import LocalSupervisor
        factory=lambda service: LocalSupervisor(str(self.root/'state.db'),str(binary),{'HOME':str(self.root)},None,lambda run:True,lambda run,role:str(self.root),lambda:True,{'mcp_servers':[],'plugins':[]},policy_hash='a'*64,engineering_root=self.root)
        class Intake:
            config={'run':'run-1','workflow':'engineering','github_owner':'owner','github_repo':'repo','github_branch':'task-1'}
        self.service,self.control=build_control(str(self.root),'x'*40,execution_mode='local',local_supervisor_factory=factory,intake_factory=lambda _:Intake(),lifecycle_factory=lambda _:ManualLifecycle(str(self.root/'state.db'),'worker','inc'))
        self.addCleanup(self.service.close); self.addCleanup(self.service.lifecycle.close)
        self.registry=EngineeringWorkspace(self.root); self.addCleanup(self.registry.close)
        self.policy=self.service.model_supervisor.policy_hash; self.args=('run-1','owner/repo',self.base,'task-1',os.geteuid(),self.policy)
    def test_exact_binding_and_exclusive_replacement(self):
        with self.assertRaises(WorkspaceError): self.registry.register(*self.args,'0'*64)
        path=self.registry.register(*self.args,self.registry.binding_hash(*self.args)); a='job-'+'1'*48; b='job-'+'2'*48
        self.assertEqual(self.registry.claim('run-1',a,self.policy),path)
        with self.assertRaises(WorkspaceError): self.registry.claim('run-1',b,self.policy)
        db=self.registry.db
        self.registry.release('run-1',a); self.assertEqual(self.registry.claim('run-1',b,self.policy),path)
    def _rejected_correction_fixture(self):
        from runner.graph import UNADOPTED_SQL
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        (self.repo/'new.txt').write_text('approved'); git(self.repo,'add','new.txt'); git(self.repo,'commit','-m','approved'); approved=git(self.repo,'rev-parse','HEAD')
        (self.repo/'new.txt').write_text('rejected'); git(self.repo,'commit','-am','rejected'); rejected=git(self.repo,'rev-parse','HEAD')
        db=self.service.graph.db; db.execute(UNADOPTED_SQL); db.execute('INSERT INTO engineering_unadopted VALUES(?,?,?,?)',('run-1',1,approved,rejected))
        return approved,rejected
    def test_rerun_after_rejected_correction_restores_approved_commit_and_keeps_rejected_work(self):
        from runner.engineering_dispatch import recover_unadopted, workspace_snapshot
        approved,rejected=self._rejected_correction_fixture()
        self.assertIn('rejected',workspace_snapshot(self.service,'run-1')['diff'])
        recover_unadopted(self.service,'run-1')
        snapshot=workspace_snapshot(self.service,'run-1')
        self.assertEqual(snapshot['head'],approved); self.assertNotIn('rejected',snapshot['diff'])
        self.assertEqual(git(self.repo,'rev-parse','refs/recovery/run-1/'+rejected),rejected)
        self.assertIsNone(self.service.graph.db.execute('SELECT 1 FROM engineering_unadopted').fetchone())
        recover_unadopted(self.service,'run-1')
    def test_recovery_refuses_binding_that_differs_from_config(self):
        from runner.engineering_dispatch import EngineeringError, recover_unadopted
        approved,rejected=self._rejected_correction_fixture()
        self.service.task_intake.config={**self.service.task_intake.config,'github_branch':'other'}
        with self.assertRaises(EngineeringError): recover_unadopted(self.service,'run-1')
        self.assertEqual(git(self.repo,'rev-parse','HEAD'),rejected)
    def test_recovery_refuses_malformed_revisions(self):
        self._rejected_correction_fixture()
        for bad in ('../x','HEAD','A'*40,''):
            with self.assertRaises(WorkspaceError): self.registry.recover_unadopted(*self.args[:6],bad,self.base)
            with self.assertRaises(WorkspaceError): self.registry.recover_unadopted(*self.args[:6],self.base,bad)
    def test_recovery_resumes_after_reset_and_refuses_unexpected_state(self):
        from runner.engineering_dispatch import EngineeringError, recover_unadopted
        approved,rejected=self._rejected_correction_fixture()
        (self.repo/'dirty.txt').write_text('operator work')
        with self.assertRaises(EngineeringError): recover_unadopted(self.service,'run-1')
        self.assertEqual(git(self.repo,'rev-parse','HEAD'),rejected); self.assertEqual((self.repo/'dirty.txt').read_text(),'operator work')
        self.assertRaises(subprocess.CalledProcessError,git,self.repo,'rev-parse','--verify','refs/recovery/run-1/'+rejected)
        (self.repo/'dirty.txt').unlink(); (self.repo/'other.txt').write_text('x'); git(self.repo,'add','other.txt'); git(self.repo,'commit','-m','other')
        with self.assertRaises(EngineeringError): recover_unadopted(self.service,'run-1')
        self.assertNotEqual(git(self.repo,'rev-parse','HEAD'),approved)
        git(self.repo,'reset','-q','--hard',rejected)
        git(self.repo,'update-ref','refs/recovery/run-1/'+rejected,rejected); git(self.repo,'reset','-q','--hard',approved)
        recover_unadopted(self.service,'run-1')
        self.assertEqual(git(self.repo,'rev-parse','HEAD'),approved)
    def test_git_verification_allows_unrelated_database_writes(self):
        import sqlite3
        from unittest.mock import patch
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        scopes=LocalScopes(str(self.root/'state.db'),Backend());scopes.close()
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        other=sqlite3.connect(str(self.root/'state.db'),timeout=.05,isolation_level=None)
        self.addCleanup(other.close)
        other.execute('CREATE TABLE claim_probe(value INTEGER)')
        verify=self.registry.verify
        def verify_with_write(*args,**kwargs):
            other.execute('INSERT INTO claim_probe VALUES(1)')
            return verify(*args,**kwargs)
        with patch.object(self.registry,'verify',side_effect=verify_with_write):
            self.registry.claim_local('run-1','job-'+'a'*48,self.policy)
        self.assertEqual(other.execute('SELECT COUNT(*) FROM claim_probe').fetchone(),(1,))

    def test_claim_rechecks_binding_and_approval_after_git_verification(self):
        from unittest.mock import patch
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        scopes=LocalScopes(str(self.root/'state.db'),Backend());scopes.close()
        self.registry.register(*self.args,self.registry.binding_hash(*self.args))
        verify=self.registry.verify
        for method in (self.registry.claim,self.registry.claim_local):
            for change in ("UPDATE engineering_workspaces SET branch='changed' WHERE run='run-1'",
                           "UPDATE engineering_workspace_approvals SET binding_hash='changed' WHERE run='run-1'",
                           "UPDATE engineering_workspaces SET assignment='other' WHERE run='run-1'",
                           "UPDATE engineering_workspaces SET assignment='other' WHERE run='run-1'; UPDATE engineering_workspaces SET assignment=NULL WHERE run='run-1'"):
                with self.subTest(method=method.__name__,change=change):
                    def verify_then_change(*args,**kwargs):
                        path=verify(*args,**kwargs)
                        for statement in change.split(';'): self.registry.db.execute(statement)
                        return path
                    with patch.object(self.registry,'verify',side_effect=verify_then_change):
                        with self.assertRaises(WorkspaceError): method('run-1','job-'+'a'*48,self.policy)
                    self.assertEqual(self.registry.db.execute('SELECT COUNT(*) FROM engineering_local_scopes').fetchone(),(0,))
                    self.registry.db.execute("UPDATE engineering_workspaces SET branch='task-1',assignment=NULL WHERE run='run-1'")
                    self.registry.db.execute('UPDATE engineering_workspace_approvals SET binding_hash=? WHERE run=?',(self.registry.binding_hash(*self.args),'run-1'))


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

    def test_completed_run_releases_only_a_terminated_bound_workspace(self):
        from types import SimpleNamespace
        from runner.control import ControlError
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        service=self.service; service.create('run-1','engineering','task'); service.enqueue('run-1')
        queue=service.lifecycle.claim(); service.claim('run-1')
        self.registry.register(*self.args,self.registry.binding_hash(*self.args)); assignment='job-'+'a'*48
        self.registry.claim('run-1',assignment,self.policy)
        backend=Backend(); service.model_supervisor=SimpleNamespace(backend=backend,engineering_root=self.root)
        scopes=LocalScopes(str(self.root/'state.db'),backend)
        try:
            unit=scopes.reserve('run-1'); backend.unit=unit; scopes.bind('run-1',unit)
            self.registry.bind_local_scope('run-1',assignment,unit)
            service.verify_terminal=lambda _:True
            service.queue.finish('run-1',queue['token'],verify_completed=lambda _:True)
            self.control.handle('x'*40,{'action':'manual_stop','run':'run-1'})
            release={'action':'release_local_workspace','run':'run-1','assignment':assignment,'unit':unit}
            with self.assertRaises(ControlError): self.control.handle('x'*40,release)
            self.control.handle('x'*40,{'action':'stop_local_scope','run':'run-1','unit':unit})
            for table,field,value in (('graph_runs','id','cancelled'),('jobs','run','uncertain')):
                self.registry.db.execute('UPDATE '+table+' SET status=? WHERE '+field+'=?',(value,'run-1'))
                with self.subTest(table=table), self.assertRaises(ControlError): self.control.handle('x'*40,release)
                self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(assignment,))
                self.registry.db.execute('UPDATE '+table+' SET status=? WHERE '+field+'=?',('completed','run-1'))
            self.registry.db.execute("INSERT INTO graph_claims(run,task_id,token,expires) SELECT id,task_id,'unexpected',? FROM graph_runs WHERE id='run-1'",(time.time()+60,))
            with self.assertRaisesRegex(WorkspaceError,'graph claim'):
                self.registry.release_local('run-1',assignment,unit,fenced=('worker','inc'))
            self.registry.db.execute("DELETE FROM graph_claims WHERE run='run-1'")
            for trigger in ("CREATE TRIGGER corrupt_recovery BEFORE INSERT ON workspace_recovery_audit BEGIN SELECT RAISE(IGNORE);END",
                            "CREATE TRIGGER corrupt_recovery AFTER INSERT ON workspace_recovery_audit BEGIN UPDATE workspace_recovery_audit SET created=0 WHERE id=NEW.id;END",
                            "CREATE TRIGGER corrupt_recovery AFTER INSERT ON workspace_recovery_audit BEGIN DELETE FROM workspace_recovery_audit WHERE id=NEW.id;END"):
                self.registry.db.execute(trigger)
                with self.subTest(trigger=trigger),self.assertRaisesRegex(WorkspaceError,'recovery audit'):
                    self.registry.release_local('run-1',assignment,unit,fenced=('worker','inc'))
                self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(assignment,))
                self.assertEqual(self.registry.db.execute('SELECT count(*) FROM workspace_recovery_audit').fetchone(),(0,))
                self.registry.db.execute('DROP TRIGGER corrupt_recovery')
            self.assertEqual(self.control.handle('x'*40,release),{'released':True})
            self.assertEqual(service.lifecycle.confirm_operator_stop('run-1','inc','terminated_and_fenced')['status'],'stopped')
            self.assertEqual(service.queue.get('run-1')['status'],'completed')
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
        backend=Backend();service.model_supervisor=SimpleNamespace(backend=backend,engineering_root=self.root)
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

    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')



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
