"""Opt-in local workspace reservation against Codex sandbox and systemd."""
import os
import unittest


@unittest.skipUnless(os.environ.get('CODEX_TEST_EXECUTABLE') and os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real local runtime not configured')
class WorkspaceReservationLiveTests(unittest.TestCase):
    def test_git_snapshot_uses_atomically_bound_scope(self):
        from runner.local_config import local_supervisor_factory
        from runner.local_config_inspect import inspect_config
        from runner.local_git_snapshot import scoped_git_snapshot
        from tests.test_engineering_workspace import EngineeringWorkspaceTests, git
        fixture=EngineeringWorkspaceTests('test_exact_binding_and_exclusive_replacement')
        fixture.setUp()
        try:
            service=fixture.service;service.create('run-1','engineering','task')
            config=inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],str(fixture.root),os.environ)
            supervisor=local_supervisor_factory(config,os.environ,str(fixture.root))(service)
            supervisor.fence=lambda _:True
            args=('run-1','owner/repo',fixture.base,'task-1',os.geteuid(),supervisor.policy_hash)
            fixture.registry.register(*args,fixture.registry.binding_hash(*args))
            assignment='job-'+'a'*48;observed=[]
            def record(unit):
                row=fixture.registry.db.execute('SELECT w.assignment,m.unit,s.state FROM engineering_workspaces w JOIN engineering_local_scopes m ON m.run=w.run AND m.assignment=w.assignment JOIN local_scopes s ON s.run=m.run AND s.unit=m.unit WHERE w.run=?',('run-1',)).fetchone()
                self.assertEqual(row,(assignment,unit,'starting'));observed.append(unit)
            with scoped_git_snapshot(supervisor,'run-1',str(fixture.repo),fixture.base,record,workspace_claim=(str(fixture.root),assignment,supervisor.policy_hash)) as (bare,unit):
                self.assertEqual(observed,[unit]);self.assertEqual(git(bare,'rev-parse','refs/heads/snapshot'),fixture.base)
                self.assertEqual(fixture.registry.db.execute('SELECT state FROM local_scopes WHERE unit=?',(unit,)).fetchone(),('terminated',))
                fixture.registry.release_local('run-1',assignment,unit)
            self.assertEqual(fixture.registry.db.execute('SELECT assignment FROM engineering_workspaces WHERE run=?',('run-1',)).fetchone(),(None,))
        finally:
            fixture.doCleanups()

    def test_approved_command_uses_atomically_bound_scope(self):
        import hashlib
        import json
        from runner.command_supervisor import CommandSupervisor
        from runner.engineering_dispatch import workspace_snapshot
        from runner.local_command import LocalCommandBackend
        from runner.local_config import local_supervisor_factory
        from runner.local_config_inspect import inspect_config
        from tests.test_engineering_workspace import EngineeringWorkspaceTests
        fixture=EngineeringWorkspaceTests('test_exact_binding_and_exclusive_replacement')
        fixture.setUp()
        try:
            service=fixture.service;service.create('run-1','engineering','task');service.enqueue('run-1')
            queue=fixture.control.handle('x'*40,{'action':'manual_claim'})['claim']
            claim=fixture.control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
            config=inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],str(fixture.root),os.environ)
            local=local_supervisor_factory(config,os.environ,str(fixture.root))(service);local.fence=lambda _:True
            service.model_supervisor=local
            args=('run-1','owner/repo',fixture.base,'task-1',os.geteuid(),local.policy_hash)
            fixture.registry.register(*args,fixture.registry.binding_hash(*args))
            spec={'argv':['/usr/bin/python3','-c','print("synthetic")'],'timeout_seconds':40,'output_limit':1024}
            policy=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            command=CommandSupervisor(service,spec,policy,LocalCommandBackend(local));service.command_supervisor=command
            service.approve('controller','command_policy',policy,'synthetic')
            snapshot=workspace_snapshot(service,'run-1')
            ref=command.execute('run-1',queue['token'],claim,snapshot)
            self.assertTrue(command.verify('run-1',fixture.base,ref,snapshot))
            row=fixture.registry.db.execute("SELECT m.unit,s.state,c.unit,c.exit_code FROM engineering_local_scopes m JOIN local_scopes s ON s.run=m.run AND s.unit=m.unit JOIN local_command_scopes c ON c.run=m.run AND c.assignment=m.assignment WHERE m.run='run-1'").fetchone()
            self.assertIsNotNone(row);self.assertEqual(row[0],row[2]);self.assertEqual(row[1::2],('terminated',0))
            self.assertEqual(fixture.registry.db.execute("SELECT assignment FROM engineering_workspaces WHERE run='run-1'").fetchone(),(None,))
        finally:
            fixture.doCleanups()
