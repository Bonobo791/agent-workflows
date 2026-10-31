import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from runner.local_push import local_push_command, LocalPushError
from tests.test_engineering_workspace import git

class LocalPushCommandTests(unittest.TestCase):
    def test_fixed_github_target_and_exact_lease(self):
        head='a'*40;old='b'*40
        args=local_push_command('/private/repo.git','/private/bin/gh','owner/repo','task-1',head,old)
        self.assertIn('--force-with-lease=refs/heads/task-1:'+old,args)
        self.assertIn('https://github.com/owner/repo.git',args)
        self.assertIn(head+':refs/heads/task-1',args)
        self.assertTrue(any('auth git-credential' in arg for arg in args))
        with self.assertRaises(LocalPushError):local_push_command('/private/repo.git','/private/bin/gh','owner/repo','../bad',head,old)
    def test_lease_rejects_remote_race_in_synthetic_local_repository(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);source=root/'source';source.mkdir();remote=root/'remote.git'
            git(source,'init','-q');(source/'file').write_text('a');git(source,'add','file');git(source,'-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qm','a');old=git(source,'rev-parse','HEAD')
            git(root,'init','--bare','-q',str(remote));git(source,'push','-q',str(remote),old+':refs/heads/task-1')
            (source/'file').write_text('b');git(source,'-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qam','b');middle=git(source,'rev-parse','HEAD')
            (source/'file').write_text('c');git(source,'-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qam','c');head=git(source,'rev-parse','HEAD');git(source,'push','-q',str(remote),middle+':refs/heads/task-1')
            args=local_push_command(str(source/'.git'),'/usr/bin/false','owner/repo','task-1',head,old);args[args.index('https://github.com/owner/repo.git')]=str(remote)
            result=subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.assertNotEqual(result.returncode,0);self.assertEqual(git(root,'--git-dir',str(remote),'rev-parse','refs/heads/task-1'),middle)

from tests.test_engineering_workspace import EngineeringWorkspaceTests
class LocalPushTransportTests(EngineeringWorkspaceTests):
    def test_exact_approval_scopes_snapshot_and_push_before_release(self):
        self._exercise_push(False)
    def test_push_with_invalid_terminal_scope_stays_uncertain(self):
        self._exercise_push(True)
    def test_interrupted_snapshot_keeps_workspace_scope_binding(self):
        self._exercise_push(False,interrupted=True)
    def test_interrupted_before_reservation_leaves_workspace_unclaimed(self):
        self._exercise_push(False,interrupted='before')
    def _exercise_push(self,corrupt,interrupted=False):
        import hashlib,json,time
        from contextlib import contextmanager
        from unittest.mock import patch
        from runner.local_config import local_supervisor_factory
        from runner.local_scope import LocalScopes
        from runner.gh_read import GHRead
        from runner.engineering_dispatch import workspace_snapshot
        from runner.local_push import LocalPushTransport
        from tests.test_local_scope import Backend
        from workflows.engineering.review_loop import BoundLedger,ReviewLoop
        service=self.service;service.create('run-1','engineering','task');service.enqueue('run-1')
        queue=self.control.handle('x'*40,{'action':'manual_claim'})['claim'];claim=self.control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
        binary=self.root/'codex';binary.write_bytes(b'synthetic');binary.chmod(0o700)
        config={'codex_executable':str(binary),'codex_sha256':hashlib.sha256(b'synthetic').hexdigest(),'disabled_integrations':{'mcp_servers':[],'plugins':[]}}
        supervisor=local_supervisor_factory(config,{'HOME':str(self.root)},str(self.root))(service);service.model_supervisor=supervisor;supervisor.fence=lambda _:True;engine=Backend();supervisor.backend=engine
        args=('run-1','owner/repo',self.base,'task-1',os.geteuid(),supervisor.policy_hash);self.registry.register(*args,self.registry.binding_hash(*args))
        (self.repo/'file').write_text('changed');git(self.repo,'add','file');git(self.repo,'commit','-m','change')
        current=workspace_snapshot(service,'run-1');target={'task':'task','repo':'owner/repo','branch':'task-1','base':'main','head':current['head'],'diff':hashlib.sha256(current['diff'].encode()).hexdigest()}
        ghpath=self.root/'gh';ghpath.write_text('#!/bin/sh\nexit 0\n');ghpath.chmod(0o700);gh=GHRead(str(ghpath),{'HOME':str(self.root)})
        class HTTP:
            head=self.base
            def request(self,method,path,body=None):return {'object':{'sha':self.head}}
            def push_exact(self,branch,sha):return transport.push_exact(branch,sha)
        http=HTTP();transport=LocalPushTransport(service,'run-1',queue['token'],claim,gh)
        lock=lambda run:run=='run-1' and service.queue.may_start(run,queue['token'])
        ledger=BoundLedger(str(self.root/'state.db'),lambda run:dict(target),lock);loop=ReviewLoop(http,'owner','repo',ledger,lock,lambda run:dict(target))
        @contextmanager
        def snapshot(s,run,workspace,head,on_reserved,*,workspace_claim):
            if interrupted=='before': raise RuntimeError('interrupted before scope reservation')
            root,assignment,policy,pr_scope=workspace_claim
            self.assertEqual((root,policy),(str(self.root),supervisor.policy_hash))
            _,unit=self.registry.claim_local(run,assignment,policy,pr_scope=pr_scope)
            if interrupted: raise RuntimeError('snapshot launch interrupted')
            scopes=LocalScopes(s.db,engine)
            try:
                engine.unit=unit;engine.state='active';scopes.bind(run,unit);scopes.stop(run,unit)
                yield str(self.repo/'.git'),unit
            finally:scopes.close()
        def push(s,run,workspace,argv,env,deadline,limit,on_reserved,on_started=None):
            scopes=LocalScopes(s.db,engine)
            try:
                unit=scopes.reserve(run);on_reserved(unit);engine.unit=unit;engine.state='active';scopes.bind(run,unit);on_started(unit)
                self.assertIn('--force-with-lease=refs/heads/task-1:'+self.base,argv);self.assertNotIn('GH_TOKEN',env)
                http.head=target['head'];scopes.stop(run,unit)
                if corrupt:scopes.db.execute('UPDATE local_scopes SET cgroup=NULL WHERE unit=?',(unit,))
                return {'outcome':'exited','exit_code':0,'stdout':b'','stderr':b'','unit':unit}
            finally:scopes.close()
        try:
            with patch('runner.local_supervisor.sandbox_preflight',return_value=True),patch('runner.local_supervisor.scope_preflight',return_value=True),patch('runner.local_git_snapshot.scoped_git_snapshot',side_effect=snapshot),patch('runner.local_command._run_scoped',side_effect=push):
                with self.assertRaises(Exception):transport.push_exact('task-1',target['head'])
                loop.approve_push('run-1',self.base,'owner',time.time()+60);action=loop.github.push_action('task-1',self.base)
                proposal=service.artifacts.put(json.dumps({'action':action,'target':target,'args':{'expected_remote':self.base}}).encode());service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(proposal,'run-1','push'))
                if interrupted=='before':
                    from workflows.engineering.github import GitHubError
                    with self.assertRaises(GitHubError):loop.push('run-1',self.base)
                    self.assertEqual(self.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
                    self.assertEqual(ledger.db.execute("SELECT status FROM external_actions WHERE action LIKE 'push:%'").fetchone(),('uncertain',))
                    self.assertEqual(service.graph.db.execute('SELECT COUNT(*) FROM local_pr_scopes').fetchone(),(0,))
                elif interrupted:
                    from workflows.engineering.github import GitHubError
                    with self.assertRaises(GitHubError):loop.push('run-1',self.base)
                    row=self.registry.db.execute("SELECT w.assignment,m.unit,s.state FROM engineering_workspaces w JOIN engineering_local_scopes m ON m.run=w.run AND m.assignment=w.assignment JOIN local_scopes s ON s.run=m.run AND s.unit=m.unit WHERE w.run='run-1'").fetchone()
                    self.assertIsNotNone(row);self.assertEqual(row[2],'starting')
                    recorded=self.registry.db.execute("SELECT unit FROM local_pr_scopes WHERE assignment=? AND run='run-1' AND kind='bundle'",(row[0],)).fetchone()
                    self.assertEqual(recorded,(row[1],))
                elif corrupt:
                    from workflows.engineering.github import GitHubError
                    with self.assertRaises(GitHubError):loop.push('run-1',self.base)
                    self.assertEqual(ledger.db.execute("SELECT status FROM external_actions WHERE action LIKE 'push:%'").fetchone(),('uncertain',))
                    self.assertIsNotNone(self.registry.db.execute('SELECT assignment FROM engineering_workspaces WHERE run=?',('run-1',)).fetchone()[0])
                else:
                    loop.push('run-1',self.base)
                    self.assertEqual(ledger.db.execute("SELECT status FROM external_actions WHERE action LIKE 'push:%'").fetchone(),('executed',))
                    self.assertEqual(self.registry.db.execute('SELECT assignment FROM engineering_workspaces WHERE run=?',('run-1',)).fetchone(),(None,))
                if not interrupted: self.assertEqual(service.graph.db.execute("SELECT kind FROM local_pr_scopes ORDER BY rowid").fetchall(),[('bundle',),('push',)])
        finally:ledger.db.close()
