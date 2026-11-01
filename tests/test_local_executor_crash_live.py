"""Opt-in crash probe using the signed-in local app server and real user scopes."""
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get('CODEX_TEST_EXECUTABLE') and os.environ.get('WORKFLOW_TEST_SYSTEMD') == '1', 'signed-in local runtime not configured')
class LocalExecutorCrashLiveTests(unittest.TestCase):
    def test_crash_preserves_scope_and_turn_identity(self):
        code = '''
import os,sys,time
from pathlib import Path
from runner.artifacts import ArtifactStore
from runner.codex_executor import CodexExecutor
from runner.graph import GraphStore,marketing_definition
from runner.local_config_inspect import inspect_config
from runner.local_process import start_local_process
from runner.local_scope import SystemdScopes
from runner.local_supervisor import LocalSupervisor
from runner.model_policy import ModelPolicy
from runner.protocol import AppServer
from runner.provenance import Provenance
root=Path(sys.argv[1]); workspace=root/'workspace'; db=str(root/'state.db'); environment=dict(os.environ)
config=inspect_config(environment['CODEX_TEST_EXECUTABLE'],str(workspace),environment)
backend=SystemdScopes(environment)
discovery=start_local_process(db,'discovery',str(workspace),config['codex_executable'],environment,backend,lambda:True,config['disabled_integrations'])
try:
 server=AppServer(discovery.open_transport(time.monotonic()+90,lambda:False)); server.initialize()
 if not discovery.verify_config(server._request) or not isinstance(server._request('account/read',{'refreshToken':False}).get('account'),dict): raise RuntimeError('local sign-in unavailable')
 model=ModelPolicy().discover(server._request)
finally: discovery.finish()
graph=GraphStore(db); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',180)
supervisor=LocalSupervisor(db,config['codex_executable'],environment,backend,lambda run:True,lambda run,role:str(workspace),lambda:True,config['disabled_integrations'])
executor=CodexExecutor(supervisor,Provenance(graph),ArtifactStore(str(root/'art')),lambda run:None)
executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='Instructions: reply in text only.\\nThink carefully about a short fictional story, then write a paragraph. Do not use tools.',instruction_length=len('Instructions: reply in text only.'),model=model,input_versions=[],output_paths=[],timeout=120)
'''
        with tempfile.TemporaryDirectory(prefix='workflow-executor-crash-') as directory:
            root=Path(directory); (root/'workspace').mkdir(mode=0o700); (root/'art').mkdir(mode=0o700)
            process=subprocess.Popen([sys.executable,'-c',code,str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+120; evidence=None
                while time.monotonic()<deadline:
                    if process.poll() is not None: self.fail('executor exited before interruption: '+process.stderr.read().decode()[-2000:])
                    try:
                        with closing(sqlite3.connect(root/'state.db',timeout=.2)) as db:
                            evidence=db.execute("SELECT s.session,s.unit,l.turn,a.model FROM local_execution_scopes s JOIN execution_link_audit l ON l.execution=s.execution JOIN execution_launch_audit a ON a.execution=s.execution WHERE l.status='observed' AND a.status='requested'").fetchone()
                    except sqlite3.OperationalError: pass
                    if evidence: break
                    time.sleep(.02)
                self.assertIsNotNone(evidence,'turn identity was not observed in time')
                process.kill(); process.wait(timeout=10)
                with closing(sqlite3.connect(root/'state.db')) as db:
                    self.assertEqual(db.execute('SELECT state FROM local_sessions WHERE session=?',(evidence[0],)).fetchone(),('active',))
                    self.assertEqual(db.execute('SELECT state FROM local_scopes WHERE unit=?',(evidence[1],)).fetchone(),('running',))
                    self.assertEqual(db.execute('SELECT count(*) FROM execution_completions WHERE session=?',(evidence[0],)).fetchone()[0],0)
                from runner.local_scope import SystemdScopes
                from runner.local_config_inspect import inspect_config
                from runner.local_supervisor import LocalSupervisor
                config=inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],str(root/'workspace'),os.environ)
                supervisor=LocalSupervisor(str(root/'state.db'),config['codex_executable'],os.environ,SystemdScopes(os.environ),lambda run:True,lambda run,role:str(root/'workspace'),lambda:True,config['disabled_integrations'])
                self.assertFalse(supervisor.can_resume('r','canonical_draft',evidence[0],{},evidence[3]))
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=10)
                process.stderr.close()
                if (root/'state.db').exists():
                    with closing(sqlite3.connect(root/'state.db')) as db:
                        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_scopes'").fetchone():
                            units=db.execute("SELECT unit,cgroup FROM local_scopes WHERE state IS NOT 'terminated'").fetchall()
                            for unit,cgroup in units:
                                if re.fullmatch(r'agent-workflows-[0-9a-f]{48}\.scope',unit):
                                    subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
                                    from runner.local_scope import SystemdScopes
                                    backend=SystemdScopes(os.environ); state=backend.inspect(unit)
                                    self.assertEqual((state['id'],state['active']),(unit,'inactive'))
                                    self.assertTrue(cgroup and backend.empty_cgroup(cgroup),'test scope cgroup still populated or unrecorded')
