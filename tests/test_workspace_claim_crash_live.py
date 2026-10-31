"""A killed local launcher must retain an exact, unresolved workspace scope."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class WorkspaceClaimCrashTests(unittest.TestCase):
    def test_crash_before_scope_launch_retains_fenced_binding(self):
        from runner.control import ControlError
        from tests.test_engineering_workspace import EngineeringWorkspaceTests
        fixture=EngineeringWorkspaceTests('test_exact_binding_and_exclusive_replacement')
        fixture.setUp()
        code='''
import os,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
from runner.local_process import start_local_process
from runner.local_scope import SystemdScopes
root,workspace,policy,assignment,marker=sys.argv[1:]
original=subprocess.Popen
def launch(args,*a,**kw):
 if args[0]=='/usr/bin/systemd-run':
  Path(marker).write_text('ready')
  time.sleep(60)
 return original(args,*a,**kw)
with patch('subprocess.Popen',side_effect=launch):
 start_local_process(str(Path(root)/'state.db'),'run-1',workspace,str(Path(root)/'codex'),{'HOME':root},SystemdScopes(os.environ),lambda:True,{'mcp_servers':[],'plugins':[]},allow_git=True,workspace_claim=(root,assignment,policy))
'''
        worker=None
        try:
            service=fixture.service;service.create('run-1','engineering','task');service.enqueue('run-1')
            fixture.control.handle('x'*40,{'action':'manual_claim'})
            fixture.registry.register(*fixture.args,fixture.registry.binding_hash(*fixture.args))
            binary=fixture.root/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            assignment='job-'+'e'*48;ready=fixture.root/'ready'
            with tempfile.TemporaryFile() as errors:
                worker=subprocess.Popen([sys.executable,'-c',code,str(fixture.root),str(fixture.repo),fixture.policy,assignment,str(ready)],stdout=subprocess.DEVNULL,stderr=errors)
                deadline=time.monotonic()+10
                while not ready.exists() and worker.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                if not ready.exists():
                    errors.seek(0);self.fail('launch boundary unavailable: '+errors.read(2000).decode(errors='replace'))
                worker.kill();self.assertEqual(worker.wait(timeout=5),-9)
                row=fixture.registry.db.execute("SELECT w.assignment,m.unit,s.state FROM engineering_workspaces w JOIN engineering_local_scopes m ON m.run=w.run AND m.assignment=w.assignment JOIN local_scopes s ON s.run=m.run AND s.unit=m.unit WHERE w.run='run-1'").fetchone()
                self.assertEqual(row[0],assignment);self.assertEqual(row[2],'starting')
                result=fixture.control.handle('x'*40,{'action':'manual_stop','run':'run-1'})
                self.assertEqual(result['local_workspace'],{'assignment':assignment,'unit':row[1]})
                with self.assertRaises(ControlError): fixture.control.handle('x'*40,{'action':'release_local_workspace','run':'run-1','assignment':assignment,'unit':row[1]})
                with self.assertRaises(ControlError): fixture.control.handle('x'*40,{'action':'manual_confirm_stopped','run':'run-1','incarnation':'inc','attestation':'terminated_and_fenced'})
        finally:
            if worker is not None and worker.poll() is None: worker.kill();worker.wait(timeout=5)
            fixture.doCleanups()
