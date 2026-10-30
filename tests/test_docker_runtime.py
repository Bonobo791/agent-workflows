import json
import socketserver
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from runner.worker import build_control

ID='a'*64
class RuntimeTests(unittest.TestCase):
    def test_worker_executor_runs_through_fake_engine_and_ingests_output(self):
        class Handler(socketserver.StreamRequestHandler):
            state='created'; label=None; output=None; turn_params=None
            def handle(self):
                request=self.rfile.readline().decode().split(); headers={}
                while True:
                    line=self.rfile.readline()
                    if line==b'\r\n': break
                    key,value=line.decode().split(':',1); headers[key.lower()]=value.strip()
                path=request[1]; body=self.rfile.read(int(headers.get('content-length','0')))
                def response(status,data=None):
                    payload=json.dumps(data).encode() if data is not None else b''
                    self.wfile.write(('HTTP/1.1 '+str(status)+' OK\r\nContent-Length: '+str(len(payload))+'\r\nContent-Type: application/json\r\n\r\n').encode()+payload); self.wfile.flush()
                if path.endswith('/containers/create'):
                    config=json.loads(body); Handler.label=config['Labels']['agent-workflows.run']
                    Handler.output=next(m['Source'] for m in config['HostConfig']['Mounts'] if m['Target']=='/output')
                    response(201,{'Id':ID})
                elif path.endswith('/json'):
                    response(200,{'Id':ID,'Config':{'Labels':{'agent-workflows.run':Handler.label}},'HostConfig':{'RestartPolicy':{'Name':'no'},'AutoRemove':False},'State':{'Running':Handler.state=='running','Status':Handler.state}})
                elif path.endswith('/start'): Handler.state='running'; response(204)
                elif '/stop?' in path: Handler.state='exited'; response(204)
                elif '/wait?' in path: response(200,{'StatusCode':0})
                elif '/attach?' in path:
                    self.wfile.write(b'HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/vnd.docker.raw-stream\r\n\r\n'); self.wfile.flush()
                    def emit(message,channel=1):
                        data=json.dumps(message).encode()+b'\n'; frame=struct.pack('>BxxxI',channel,len(data))+data
                        self.wfile.write(frame[:3]); self.wfile.write(frame[3:]); self.wfile.flush()
                    for line in self.rfile:
                        msg=json.loads(line); method=msg.get('method'); ident=msg.get('id')
                        if method=='initialize': emit({'id':ident,'result':{}})
                        elif method=='thread/start': emit({'id':ident,'result':{'thread':{'id':'thread-one'}}})
                        elif method=='turn/start':
                            Handler.turn_params=msg['params']
                            Path(Handler.output,'draft.txt').write_text('synthetic output')
                            emit({'id':ident,'result':{'turn':{'id':'turn-one'}}})
                            emit({'message':'stderr'},2)
                            emit({'method':'item/agentMessage/delta','params':{'threadId':'thread-one','turnId':'wrong','delta':'wrong'}})
                            emit({'method':'item/agentMessage/delta','params':{'threadId':'thread-one','turnId':'turn-one','delta':'correct'}})
                            emit({'method':'turn/completed','params':{'threadId':'thread-one','turn':{'id':'turn-one','status':'completed'}}}); break
                else: response(404)
        class Server(socketserver.ThreadingUnixStreamServer): daemon_threads=True
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700); socket=str(Path(root)/'engine.sock')
            server=Server(socket,Handler); thread=threading.Thread(target=server.serve_forever); thread.start()
            config={'socket':socket,'image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            from runner.manual_lifecycle import ManualLifecycle
            service,control=build_control(root,'x'*40,docker_config=config,lifecycle_factory=lambda service: ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                class SyntheticDelivery:
                    def ready(self): return True
                    def deliver(self,assignment,cid): return True
                    def verify_home(self,path): return True
                service.docker_supervisor.credential_delivery=SyntheticDelivery()
                service.docker_supervisor.provider_ready=lambda:True  # synthetic verifier only
                service.model_policy.discover(lambda *_:{'data':[{'model':'fake-terra','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None})
                service.approve('controller','docker_config',service.docker_supervisor.config.policy_hash,'operator')
                service.create('run-1','marketing','task'); service.enqueue('run-1'); service.lifecycle.claim(); claim=service.claim('run-1')
                result=service.docker_executor.execute(run='run-1',role='canonical_draft',claim=claim,skills={},prompt='p',model='fake-terra',input_versions=['input'],output_paths=['draft.txt'])
                self.assertEqual(Handler.turn_params['model'],'fake-terra'); self.assertEqual(Handler.turn_params['effort'],'medium')
                self.assertEqual(result['output'],'correct'); self.assertEqual(service.artifacts.get(result['outputs'][0]),b'synthetic output')
                self.assertEqual(service.graph.db.execute('SELECT count(*) FROM completed_turn_outputs').fetchone()[0],1)
                self.assertEqual(service.graph.db.execute('SELECT state FROM docker_assignments').fetchone()[0],'terminated')
            finally: service.lifecycle.close(); service.close(); server.shutdown(); thread.join(); server.server_close()
    def test_fake_engine_replaces_writer_and_keeps_reviewer_separate(self):
        class Handler(socketserver.StreamRequestHandler):
            containers={}; specs=[]
            def handle(self):
                request=self.rfile.readline().decode().split(); headers={}
                while True:
                    line=self.rfile.readline()
                    if line==b'\r\n': break
                    key,value=line.decode().split(':',1); headers[key.lower()]=value.strip()
                path=request[1]; body=self.rfile.read(int(headers.get('content-length','0')))
                def response(status,data=None):
                    payload=json.dumps(data).encode() if data is not None else b''
                    self.wfile.write(('HTTP/1.1 '+str(status)+' OK\r\nContent-Length: '+str(len(payload))+'\r\nContent-Type: application/json\r\n\r\n').encode()+payload); self.wfile.flush()
                if path.endswith('/containers/create'):
                    spec=json.loads(body); cid=format(len(Handler.specs)+1,'064x'); Handler.specs.append(spec)
                    mounts={m['Target']:m['Source'] for m in spec['HostConfig']['Mounts']}
                    Handler.containers[cid]={'state':'created','label':spec['Labels']['agent-workflows.run'],'mounts':mounts}
                    response(201,{'Id':cid}); return
                cid=path.split('/')[3]; item=Handler.containers[cid]
                if path.endswith('/json'): response(200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run':item['label']}},'State':{'Running':item['state']=='running','Status':item['state']}})
                elif path.endswith('/start'): item['state']='running'; response(204)
                elif '/stop?' in path: item['state']='exited'; response(204)
                elif '/wait?' in path: response(200,{'StatusCode':0})
                elif '/attach?' in path:
                    self.wfile.write(b'HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/vnd.docker.raw-stream\r\n\r\n'); self.wfile.flush()
                    def emit(message):
                        data=json.dumps(message).encode()+b'\n'; self.wfile.write(struct.pack('>BxxxI',1,len(data))+data); self.wfile.flush()
                    for line in self.rfile:
                        msg=json.loads(line); method=msg.get('method'); ident=msg.get('id')
                        if method=='initialize': emit({'id':ident,'result':{}})
                        elif method=='thread/start':
                            tid='thread-'+cid[-2:]; Path(item['mounts']['/codex-home'],'synthetic-state').write_text(tid)
                            emit({'id':ident,'result':{'thread':{'id':tid}}})
                        elif method=='thread/resume':
                            tid=msg['params']['threadId']; saved=Path(item['mounts']['/codex-home'],'synthetic-state').read_text()
                            if saved!=tid: emit({'id':ident,'error':{'message':'missing'}}); break
                            emit({'id':ident,'result':{'thread':{'id':tid}}})
                        elif method=='turn/start':
                            tid=msg['params']['threadId']; Path(item['mounts']['/output'],'draft.txt').write_text('synthetic')
                            emit({'id':ident,'result':{'turn':{'id':'turn-'+cid[-2:]}}})
                            emit({'method':'item/agentMessage/delta','params':{'threadId':tid,'turnId':'turn-'+cid[-2:],'delta':'correct'}})
                            emit({'method':'turn/completed','params':{'threadId':tid,'turn':{'id':'turn-'+cid[-2:],'status':'completed'}}}); break
                else: response(404)
        class Server(socketserver.ThreadingUnixStreamServer): daemon_threads=True
        class SyntheticDelivery:
            def __init__(self): self._synthetic_token='synthetic-oauth'
            def ready(self): return True
            def deliver(self,assignment,cid): return self._synthetic_token=='synthetic-oauth'  # fake boundary; no delivery to the app-server
            def verify_home(self,path): return True
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700); socket=str(Path(root)/'engine.sock')
            server=Server(socket,Handler); thread=threading.Thread(target=server.serve_forever); thread.start()
            config={'socket':socket,'image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)}
            from runner.manual_lifecycle import ManualLifecycle
            service,control=build_control(root,'x'*40,docker_config=config,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.docker_supervisor.credential_delivery=SyntheticDelivery(); service.docker_supervisor.provider_ready=lambda:True
                service.model_policy.discover(lambda *_:{'data':[{'model':'fake-terra','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None})
                service.approve('controller','docker_config',service.docker_supervisor.config.policy_hash,'operator')
                service.create('run-1','marketing','task'); service.enqueue('run-1'); service.lifecycle.claim(); claim=service.claim('run-1')
                args=dict(run='run-1',role='canonical_draft',claim=claim,skills={'writer':'hash'},prompt='p',model='fake-terra',input_versions=['input'],output_paths=['draft.txt'])
                first=service.docker_executor.execute(**args); service.graph.bind_session('run-1','canonical_draft',first['session_id'],claim)
                self.assertFalse(service.docker_supervisor.can_resume('run-1','canonical_draft',first['session_id'],{'writer':'wrong'},'fake-terra'))
                self.assertFalse(service.docker_supervisor.can_resume('run-1','canonical_review',first['session_id'],{'writer':'hash'},'fake-terra'))
                second=service.docker_executor.execute(**args,session_id=first['session_id'])
                reviewer=service.docker_executor.execute(**{**args,'role':'canonical_review','skills':{'reviewer':'hash'}})
                homes=[next(m['Source'] for m in spec['HostConfig']['Mounts'] if m['Target']=='/codex-home') for spec in Handler.specs]
                self.assertEqual(first['session_id'],second['session_id']); self.assertNotEqual(first['session_id'],reviewer['session_id'])
                self.assertEqual(homes[0],homes[1]); self.assertNotEqual(homes[1],homes[2])
                self.assertTrue(all(not any('ACCESS_TOKEN=' in item for item in spec.get('Env',[])) for spec in Handler.specs))
            finally: service.lifecycle.close(); service.close(); server.shutdown(); thread.join(); server.server_close()
