"""Opt-in signed-in worker checkpoint and restart with synthetic content."""
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get('CODEX_TEST_EXECUTABLE') and os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','signed-in local runtime not configured')
class WorkerCheckpointLiveTests(unittest.TestCase):
    def test_checkpoint_and_restart_resume_same_signed_in_session(self):
        code='''
import json,os,time
from pathlib import Path
from runner.control import ControlPlane
from runner.local_config import local_supervisor_factory
from runner.local_config_inspect import inspect_config
from runner.manual_lifecycle import ManualLifecycle
from runner.model_policy import ModelPolicy
from runner.protocol import AppServer
from runner.service import Service
from tests.test_local_session_live import run_writer_turn
import runner.worker as worker

def fixture_control(root,secret,**kwargs):
 service=Service(root);service.state_db_path=str(Path(root)/'state.db')
 service.lifecycle=ManualLifecycle(service.state_db_path,'fixture',os.environ['MANUAL_WORKER_INCARNATION'])
 config=inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],str(root),os.environ)
 supervisor=local_supervisor_factory(config,os.environ,str(root))(service)
 supervisor.fence=lambda run:worker.manual_fence_ready(service,run)
 service.model_supervisor=supervisor
 class Intake:
  config={'run':'r'}
  def submit_configured(self):service.create('r','marketing','synthetic');service.enqueue('r')
 class Dispatcher:
  def step(self,run,queue,claim):
   marker=Path(root)/('phase-'+os.environ['TEST_PHASE']+'.json')
   if marker.exists(): return {'stage':'freeze','status':'awaiting_approval'}
   try:
    model_file=Path(root)/'model.json'
    if model_file.exists(): model=json.loads(model_file.read_text())['model']
    else:
     handle=supervisor.start_execution(run,'model_discovery',None,{},'catalog-discovery')
     try:
      server=AppServer(handle.open_transport(time.monotonic()+90,lambda:False));server.initialize()
      model=ModelPolicy().discover(server._request)
     finally:handle.finish()
     model_file.write_text(json.dumps({'model':model}))
    phase=os.environ['TEST_PHASE'];session=service.graph.session(run,'canonical_draft')
    if phase=='2' and session is None: raise RuntimeError('persisted session unavailable')
    prompt='Remember this exact token for the next turn: cobalt-orchid. Reply OK. Do not use tools.' if phase=='1' else 'Reply with only the exact token I asked you to remember. Do not use tools.'
    handle=supervisor.start_execution(run,'canonical_draft',session,{},model)
    try:actual,output=run_writer_turn(handle,model,session,prompt)
    finally:handle.finish()
    service.graph.bind_session(run,'canonical_draft',actual,claim)
    marker.write_text(json.dumps({'session':actual,'output':output,'queue':queue,'claim':claim}))
    return {'stage':'freeze','status':'awaiting_approval'}
   except Exception as error:
    (Path(root)/'stage-error').write_text(type(error).__name__)
    raise
 service.task_intake=Intake();service.stage_dispatcher=Dispatcher()
 return service,ControlPlane(service,secret)
worker.build_control=fixture_control
worker.main()
'''
        with tempfile.TemporaryDirectory(prefix='workflow-checkpoint-live-') as directory:
            root=Path(directory);secret=root/'secret';secret.write_text('x'*40);secret.chmod(0o600)
            for name,value in (('task',{'run':'r'}),('roles',{}),('runtime',{})):
                path=root/(name+'.json');path.write_text(json.dumps(value));path.chmod(0o600)
            env=os.environ.copy();env.update(CONTROL_SECRET_FILE=str(secret),WORKFLOW_DATA_DIR=str(root),WORKFLOW_EXECUTION_MODE='disabled',WORKFLOW_LIFECYCLE_MODE='manual',MANUAL_WORKER_TARGET='fixture',MANUAL_WORKER_INCARNATION='one',WORKFLOW_TASK_CONFIG_FILE=str(root/'task.json'),WORKFLOW_ROLE_CONFIG_FILE=str(root/'roles.json'),DOCKER_RUNTIME_CONFIG_FILE=str(root/'runtime.json'))
            env.pop('WORKFLOW_CONTROL_DIR',None)
            worker=None
            def request(body):
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                    client.settimeout(5);client.connect(str(root/'control.sock'))
                    client.sendall(json.dumps({'secret':'x'*40,'request':body}).encode()+b'\n')
                    with client.makefile('rb') as stream: response=json.loads(stream.readline())
                self.assertTrue(response['ok'],response);return response['result']
            def wait_for(predicate,timeout):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline and worker.poll() is None:
                    if (root/'stage-error').exists():self.fail('fixture stage failed: '+(root/'stage-error').read_text())
                    if predicate():return
                    time.sleep(.05)
                self.fail('worker did not reach expected state')
            def start(phase):
                nonlocal worker
                env['TEST_PHASE']=phase
                worker=subprocess.Popen([sys.executable,'-c',code],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                wait_for(lambda:(root/'control.sock').is_socket(),20)
                self.assertTrue(request({'action':'start_configured_auto'})['started'])
                wait_for(lambda:(root/('phase-'+phase+'.json')).exists(),120)
                wait_for(lambda:request({'action':'automatic_status'}).get('checkpoint_available'),10)
            try:
                start('1');first=json.loads((root/'phase-1.json').read_text())
                self.assertTrue(request({'action':'checkpoint_configured_auto','run':'r'})['pending'])
                wait_for(lambda:request({'action':'automatic_status'})['status']=='checkpointed',10)
                self.assertEqual(request({'action':'register_checkpoint_replacement','run':'r','old_incarnation':'one','new_incarnation':'two'}),{'incarnation':'two'})
                env['MANUAL_WORKER_INCARNATION']='two'
                competing=subprocess.Popen([sys.executable,'-c',code],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                self.assertNotEqual(competing.wait(timeout=10),0)
                self.assertIsNone(worker.poll())
                worker.terminate();self.assertEqual(worker.wait(timeout=10),0)
                start('2');second=json.loads((root/'phase-2.json').read_text())
                self.assertEqual(first['session'],second['session']);self.assertIn('cobalt-orchid',second['output'].lower())
                self.assertNotEqual(first['queue'],second['queue']);self.assertNotEqual(first['claim'],second['claim'])
                with sqlite3.connect(root/'state.db') as db:self.assertEqual(db.execute("SELECT incarnation FROM manual_assignments WHERE run='r'").fetchone(),('two',))
            finally:
                if worker is not None and worker.poll() is None:
                    worker.terminate()
                    try:worker.wait(timeout=10)
                    except subprocess.TimeoutExpired:worker.kill();worker.wait(timeout=5)
                if (root/'state.db').exists():
                    with sqlite3.connect(root/'state.db') as db:
                        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        if 'local_scopes' in tables:
                            units=[row[0] for row in db.execute("SELECT unit FROM local_scopes WHERE state IS NOT 'terminated'")]
                            for unit in units:
                                if isinstance(unit,str) and re.fullmatch(r'agent-workflows-[0-9a-f]{48}\.scope',unit):
                                    subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
