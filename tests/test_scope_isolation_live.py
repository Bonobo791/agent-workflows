"""Opt-in, narrow checks of sandbox access to local scope controls."""
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest


@unittest.skipUnless(os.environ.get('CODEX_TEST_EXECUTABLE') and os.environ.get('WORKFLOW_TEST_SYSTEMD') == '1',
                     'real local runtime not configured')
class ScopeIsolationLiveTests(unittest.TestCase):
    def test_sandbox_cannot_open_tested_parent_cgroup_or_connect_user_bus(self):
        from runner.local_command import run_scoped_sandbox
        from runner.local_config_inspect import inspect_config
        from runner.local_scope import SystemdScopes, valid_scope_identity

        code = '''import os,socket
uid=os.getuid()
path='/sys/fs/cgroup/user.slice/user-%d.slice/user@%d.service/app.slice/cgroup.procs'%(uid,uid)
try:
 fd=os.open(path,os.O_WRONLY);os.close(fd);print('parent_cgroup_open=allowed')
except OSError as error: print('parent_cgroup_open=denied:%s'%error.errno)
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
try:
 s.settimeout(2);s.connect('/run/user/%d/bus'%uid);print('user_bus_connect=allowed')
except OSError as error: print('user_bus_connect=denied:%s'%error.errno)
finally:s.close()
'''
        temporary=tempfile.TemporaryDirectory(prefix='workflow-scope-isolation-',delete=False)
        root=Path(temporary.name);environment=dict(os.environ);backend=SystemdScopes(environment);units=[]
        try:
            workspace=root/'workspace';workspace.mkdir(mode=0o700)
            config=inspect_config(environment['CODEX_TEST_EXECUTABLE'],str(workspace),environment)
            supervisor=SimpleNamespace(db=str(root/'state.db'),backend=backend,
                executable=config['codex_executable'],environment=environment,
                disabled_integrations=config['disabled_integrations'],fence=lambda _:True)
            result=run_scoped_sandbox(supervisor,'isolation-probe',str(workspace),
                ['/usr/bin/python3','-c',code],time.monotonic()+60,4096,units.append)
            self.assertEqual(result['exit_code'],0)
            output=result['stdout'].decode('ascii').splitlines()
            self.assertEqual(len(output),2)
            self.assertTrue(output[0].startswith('parent_cgroup_open=denied:'),output)
            self.assertTrue(output[1].startswith('user_bus_connect=denied:'),output)
        finally:
            try:
                for unit in units:
                    with sqlite3.connect(root/'state.db') as db:
                        row=db.execute('SELECT state,invocation,cgroup FROM local_scopes WHERE unit=?',(unit,)).fetchone()
                    observed=backend.inspect(unit)
                    if observed['active']=='active':
                        if not row or row[0]!='running' or observed['invocation']!=row[1] or observed['cgroup']!=row[2]:
                            raise RuntimeError('active scope identity is not verified')
                        backend.stop(unit)
                        observed=backend.inspect(unit)
                    if (not row or row[0]!='terminated' or not valid_scope_identity(unit,row[1],row[2])
                            or observed['id']!=unit or observed['active']!='inactive'
                            or observed['load'] not in ('loaded','not-found')
                            or (observed['load']=='loaded' and observed['invocation'] not in ('',row[1]))
                            or not backend.empty_cgroup(row[2])):
                        raise RuntimeError('scope cleanup is not verified')
            except Exception as error:
                error.add_note('Private isolation fixture retained at '+str(root))
                raise
            temporary.cleanup()
