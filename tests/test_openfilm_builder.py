import hashlib
import io
import json
import socketserver
import struct
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from runner.artifacts import ArtifactStore
from runner.openfilm_builder import _attach_media, _media
from runner.worker import build_control
from runner.manual_lifecycle import ManualLifecycle


class MediaAttachmentTests(unittest.TestCase):
    def test_media_is_private_per_asset_and_reproducible(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root)
            image=b'\x89PNG\r\n\x1a\nexample'
            image_ref=store.put(image); rights=store.put(b'rights evidence')
            brief={'assets':[{'media':[{'artifact_ref':image_ref,'rights_ref':rights}]},{'media':[]}]}
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as z:
                z.writestr('projects/0/film.html','film'); z.writestr('projects/1/film.html','film')
            project=store.put(raw.getvalue())
            packaged=_attach_media(store,project,brief)
            self.assertEqual(packaged,_attach_media(store,project,brief))
            with zipfile.ZipFile(io.BytesIO(packaged)) as z:
                self.assertEqual(z.read('projects/0/media/'+image_ref+'.png'),image)
                self.assertNotIn('projects/1/media/'+image_ref+'.png',z.namelist())
            self.assertEqual(hashlib.sha256(image).hexdigest(),image_ref)
            brief['assets'][0]['media'][0]['rights_ref']='0'*64
            with self.assertRaises(Exception): _media(store,brief)

    def test_author_cannot_replace_supplied_media(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root); image=store.put(b'\x89PNG\r\n\x1a\nimage'); rights=store.put(b'rights')
            brief={'assets':[{'media':[{'artifact_ref':image,'rights_ref':rights}]}]}
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as z:
                z.writestr('film.html','film'); z.writestr('media/fake.png','fake')
            with self.assertRaises(ValueError): _attach_media(store,store.put(raw.getvalue()),brief)

    def test_unknown_media_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root); item={'artifact_ref':store.put(b'unknown'), 'rights_ref':store.put(b'rights')}
            with self.assertRaises(ValueError): _media(store,{'assets':[{'media':[item]}]})


class BuilderRuntimeTests(unittest.TestCase):
    def test_builder_binds_session_project_media_and_terminal_evidence(self):
        class Handler(socketserver.StreamRequestHandler):
            state='created'; label=None; output=None
            def handle(self):
                request=self.rfile.readline().decode().split(); headers={}
                while True:
                    line=self.rfile.readline()
                    if line==b'\r\n': break
                    key,value=line.decode().split(':',1); headers[key.lower()]=value.strip()
                path=request[1]; body=self.rfile.read(int(headers.get('content-length','0')))
                def response(status,data=None):
                    payload=json.dumps(data).encode() if data is not None else b''
                    self.wfile.write(('HTTP/1.1 '+str(status)+' OK\r\nContent-Length: '+str(len(payload))+'\r\n\r\n').encode()+payload); self.wfile.flush()
                if path.endswith('/containers/create'):
                    spec=json.loads(body); Handler.label=spec['Labels']['agent-workflows.run']
                    Handler.output=next(m['Source'] for m in spec['HostConfig']['Mounts'] if m['Target']=='/output')
                    response(201,{'Id':'a'*64})
                elif path.endswith('/json'):
                    response(200,{'Id':'a'*64,'Config':{'Labels':{'agent-workflows.run':Handler.label}},'HostConfig':{'RestartPolicy':{'Name':'no'},'AutoRemove':False},'State':{'Running':Handler.state=='running','Status':Handler.state}})
                elif path.endswith('/start'): Handler.state='running'; response(204)
                elif '/stop?' in path: Handler.state='exited'; response(204)
                elif '/wait?' in path: response(200,{'StatusCode':0})
                elif '/attach?' in path:
                    self.wfile.write(b'HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/vnd.docker.raw-stream\r\n\r\n'); self.wfile.flush()
                    def emit(message):
                        data=json.dumps(message).encode()+b'\n'; self.wfile.write(struct.pack('>BxxxI',1,len(data))+data); self.wfile.flush()
                    for line in self.rfile:
                        msg=json.loads(line); method=msg.get('method'); ident=msg.get('id')
                        if method=='initialize': emit({'id':ident,'result':{}})
                        elif method=='thread/start': emit({'id':ident,'result':{'thread':{'id':'builder-session'}}})
                        elif method=='turn/start':
                            Handler.prompt=msg['params']['input'][0]['text']
                            inputs=json.loads(msg['params']['input'][0]['text'].split('\n',1)[1])
                            target=Path(Handler.output)/'project.zip'
                            with zipfile.ZipFile(target,'w') as z: z.writestr('film.html','<html>fixture</html>')
                            result={'stage':'asset_production','status':'passed','input_versions':inputs['input_versions'],'outputs':[hashlib.sha256(target.read_bytes()).hexdigest()],'findings':[]}
                            emit({'id':ident,'result':{'turn':{'id':'builder-turn'}}})
                            emit({'method':'item/agentMessage/delta','params':{'threadId':'builder-session','turnId':'builder-turn','delta':json.dumps(result)}})
                            emit({'method':'turn/completed','params':{'threadId':'builder-session','turn':{'id':'builder-turn','status':'completed'}}}); break
                else: response(404)
        class Server(socketserver.ThreadingUnixStreamServer): daemon_threads=True
        class Delivery:
            def ready(self): return True
            def deliver(self,assignment,cid): return True
            def verify_home(self,path): return True
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); jobs=root/'jobs'; jobs.mkdir(mode=0o700)
            skills=root/'skills'/'author'; skills.mkdir(parents=True); (skills/'SKILL.md').write_text('Create an editable project.')
            server=Server(str(root/'engine.sock'),Handler); thread=threading.Thread(target=server.serve_forever); thread.start()
            config={'socket':str(root/'engine.sock'),'image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            service,_=build_control(str(root),'x'*40,docker_config=config,lifecycle_factory=lambda _:ManualLifecycle(str(root/'state.db'),'worker','inc'),role_config={'skill_roots':[str(root/'skills')],'required_skills':{'asset_production':['author']}},openfilm_policy_verifier=lambda _:True,openfilm_builder_policy_verifier=lambda _:True)
            try:
                supervisor=service.docker_supervisor; supervisor.credential_delivery=Delivery(); supervisor.provider_ready=lambda:True
                service.model_policy.discover(lambda *_:{'data':[{'model':'fake-terra','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None})
                builder=service.openfilm_backend.builder
                service.approve('controller','docker_config',supervisor.config.policy_hash,'operator')
                service.approve('controller','openfilm_builder_policy',builder.policy_hash,'operator')
                service.create('run','marketing','task'); service.enqueue('run'); job=service.lifecycle.claim(); claim=service.claim('run')
                content=service.artifacts.put(b'Approved text'); media=service.artifacts.put(b'\x89PNG\r\n\x1a\nfixture'); rights=service.artifacts.put(b'rights')
                brief={'content_ref':content,'assets':[{'media':[{'artifact_ref':media,'rights_ref':rights}]}]}
                brief_ref=service.artifacts.put(json.dumps(brief).encode()); attempt='1'*64
                service.graph.db.execute("INSERT INTO openfilm_render_jobs(assignment,task_run,attempt_id,policy_hash,state,brief_ref) VALUES(?,?,?,?,?,?)",('of-'+attempt,'run',attempt,service.openfilm_backend.policy_hash,'building',brief_ref))
                built=builder.build(run='run',attempt_id=attempt,brief_ref=brief_ref,brief=brief,canonical='Approved text',graph_claim=claim,queue_claim=job['token'])
                self.assertIn('Render each video\'s supplied captions visibly',Handler.prompt)
                self.assertTrue(builder.verify('run',attempt,brief_ref,built['project_ref'],built['proof_ref']))
                with zipfile.ZipFile(io.BytesIO(service.artifacts.get(built['project_ref']))) as z: self.assertEqual(z.read('media/'+media+'.png'),service.artifacts.get(media))
                self.assertFalse(builder.verify('run','2'*64,brief_ref,built['project_ref'],built['proof_ref']))
                altered=service.artifacts.put(json.dumps({'content_ref':content,'assets':[{'media':[]}]}).encode())
                self.assertFalse(builder.verify('run',attempt,altered,built['project_ref'],built['proof_ref']))
                (skills/'SKILL.md').write_text('Changed instruction')
                self.assertFalse(builder.verify('run',attempt,brief_ref,built['project_ref'],built['proof_ref']))
                (skills/'SKILL.md').write_text('Create an editable project.')
                self.assertTrue(builder.verify('run',attempt,brief_ref,built['project_ref'],built['proof_ref']))
                service.graph.db.execute("DELETE FROM docker_terminal_evidence")
                self.assertFalse(builder.verify('run',attempt,brief_ref,built['project_ref'],built['proof_ref']))
            finally:
                service.lifecycle.close(); service.close(); server.shutdown(); thread.join(); server.server_close()
