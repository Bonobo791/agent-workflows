import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runner.local_scope import LocalScopes
from runner.local_supervisor import LocalSupervisor
from runner.manual_lifecycle import ManualLifecycle
from runner.process_transport import ProcessTransport
from runner.worker import build_control
from tests.test_local_scope import Backend

FAKE = r'''import hashlib,json,os,sys,zipfile
from pathlib import Path
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); i=m.get('id')
 if method=='initialize': send({'id':i,'result':{}})
 elif method=='thread/start': send({'id':i,'result':{'thread':{'id':'builder-thread'}}})
 elif method=='turn/start':
  payload=json.loads(m['params']['input'][0]['text'].split('\n',1)[1]); target=Path(os.environ['OUTPUT'])/'project.zip'
  with zipfile.ZipFile(target,'w') as z: z.writestr('film.html','<html>synthetic</html>')
  result={'stage':'asset_production','status':'passed','input_versions':payload['input_versions'],'outputs':[hashlib.sha256(target.read_bytes()).hexdigest()],'findings':[]}
  send({'id':i,'result':{'turn':{'id':'builder-turn'}}})
  send({'method':'item/agentMessage/delta','params':{'threadId':'builder-thread','turnId':'builder-turn','delta':json.dumps(result)}})
  send({'method':'turn/completed','params':{'threadId':'builder-thread','turn':{'id':'builder-turn','status':'completed'}}})
'''

class LocalBuilderTests(unittest.TestCase):
    def test_local_builder_binds_scope_and_rejects_changed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); jobs=root/'jobs';jobs.mkdir(mode=0o700)
            skills=root/'skills'/'author';skills.mkdir(parents=True);(skills/'SKILL.md').write_text('Make a synthetic film.')
            binary=root/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            script=root/'fake.py';script.write_text(FAKE);backend=Backend()
            factory=lambda service:LocalSupervisor(str(root/'state.db'),str(binary),{'HOME':str(root)},backend,lambda run:True,lambda run,role:str(root),lambda:True,{'mcp_servers':[],'plugins':[]},policy_hash='a'*64)
            runtime={'socket':'/tmp/not-connected.sock','image':'sha256:'+'1'*64,'network':'none','job_root':str(jobs),'engine':'podman-rootless'}
            service,_=build_control(str(root),'x'*40,execution_mode='local',local_supervisor_factory=factory,docker_config=runtime,lifecycle_factory=lambda _:ManualLifecycle(str(root/'state.db'),'worker','inc'),role_config={'skill_roots':[str(root/'skills')],'required_skills':{'asset_production':['author']}},openfilm_policy_verifier=lambda _:True,openfilm_builder_policy_verifier=lambda _:True)
            class Handle:
                def __init__(self,run):
                    self.run=run;self.output_dir=str(root/'out');Path(self.output_dir).mkdir(mode=0o700);self.output_path_prefix=self.output_dir
                    scopes=LocalScopes(str(root/'state.db'),backend);self.unit=scopes.reserve(run);backend.unit=self.unit;scopes.bind(run,self.unit);scopes.close()
                    self.process=subprocess.Popen([sys.executable,str(script)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={**os.environ,'OUTPUT':self.output_dir})
                def assert_active(self): return True
                def open_transport(self,deadline,cancelled): return ProcessTransport(self.process,deadline,cancelled)
                def verify_config(self,request): return True
                def finish(self):
                    self.process.stdin.close(); self.process.wait(timeout=5); self.process.stdout.close()
                    scopes=LocalScopes(str(root/'state.db'),backend)
                    try:return scopes.stop(self.run,self.unit)
                    finally:scopes.close()
                def cancel(self): return self.finish()
            try:
                service.model_policy.discover(lambda *_:{'data':[{'model':'fake-terra','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None})
                builder=service.openfilm_backend.builder
                self.assertIs(builder.supervisor,service.model_supervisor)
                service.approve('controller','docker_config',service.docker_supervisor.config.policy_hash,'operator')
                service.approve('controller','openfilm_builder_policy',builder.policy_hash,'operator')
                service.create('run','marketing','task');service.enqueue('run');job=service.lifecycle.claim();claim=service.claim('run')
                content=service.artifacts.put(b'Approved text');brief={'content_ref':content,'assets':[{'media':[]}]};brief_ref=service.artifacts.put(json.dumps(brief).encode());attempt='1'*64
                service.graph.db.execute("INSERT INTO openfilm_render_jobs(assignment,task_run,attempt_id,policy_hash,state,brief_ref) VALUES(?,?,?,?,?,?)",('of-'+attempt,'run',attempt,service.openfilm_backend.policy_hash,'building',brief_ref))
                with patch.object(service.model_supervisor,'preflight',return_value=True),patch('runner.local_supervisor.start_local_process',side_effect=lambda db,run,*args,**kwargs:Handle(run)):
                    built=builder.build(run='run',attempt_id=attempt,brief_ref=brief_ref,brief=brief,canonical='Approved text',graph_claim=claim,queue_claim=job['token'])
                self.assertTrue(builder.verify('run',attempt,brief_ref,built['project_ref'],built['proof_ref']))
                with patch.object(service.model_supervisor,'verify_policy',return_value=False):
                    self.assertFalse(builder.verify('run',attempt,brief_ref,built['project_ref'],built['proof_ref']))
                service.graph.db.execute("UPDATE local_scopes SET state='running'")
                self.assertFalse(builder.verify('run',attempt,brief_ref,built['project_ref'],built['proof_ref']))
            finally:service.lifecycle.close();service.close()
