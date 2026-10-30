import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from runner.artifacts import ArtifactStore
from runner.codex_executor import CodexExecutor
from runner.graph import GraphStore, marketing_definition
from runner.provenance import Provenance

SCRIPT=str(Path(__file__).parent/'fixtures'/'fake_app_server.py')
class Handle:
    def __init__(self,root,mode,thread,turn,role):
        self.output_dir=root; self.process=subprocess.Popen([sys.executable,SCRIPT,mode],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=dict(os.environ,FAKE_OUTPUT=root,FAKE_THREAD=thread,FAKE_TURN=turn,FAKE_ROLE=role))
    def finish(self):
        self.process.stdin.close()
        try: code=self.process.wait(timeout=2)
        except subprocess.TimeoutExpired: return False
        self.process.stdout.close(); return code==0
    def cancel(self):
        self.process.kill(); self.process.wait(timeout=2)
        self.process.stdin.close(); self.process.stdout.close()
class Supervisor:
    def __init__(self,root,mode='ok',thread='thread-one',turn='turn-one'): self.root,self.mode,self.thread,self.turn=root,mode,thread,turn
    def preflight(self): return True  # fixture only, no isolation is asserted for production
    def can_resume(self,run,role,session): return True  # synthetic app-server fixture
    def start(self,run,role): return Handle(self.root,self.mode,self.thread,self.turn,role)
class ExecutorTests(unittest.TestCase):
    def test_hung_supervisor_start_is_bounded_and_fenced(self):
        import threading
        release=threading.Event()
        class Hung:
            def preflight(self): return True
            def start(self,run,role): release.wait(2); raise RuntimeError('no worker')
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            fenced=[]; executor=CodexExecutor(Hung(),Provenance(graph),ArtifactStore(root),fenced.append)
            try:
                with self.assertRaises(Exception): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=[],timeout=.05)
                self.assertEqual(fenced,['r'])
            finally: release.set(); graph.close()

    def test_marketing_role_adapter_uses_actual_subprocess_executor(self):
        import hashlib
        from workflows.marketing.adapters import MarketingSessionAdapter
        class Resolver:
            def resolve(self,names): return {name:{'content':'synthetic skill','sha256':hashlib.sha256(b'synthetic skill').hexdigest()} for name in names}
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            store=ArtifactStore(str(art)); source=store.put(b'synthetic brief')
            executor=CodexExecutor(Supervisor(str(output)),Provenance(graph),store,lambda run:None)
            adapter=MarketingSessionAdapter(store,executor,Resolver(),{'canonical_draft':['writer']})
            result=adapter.run('r','canonical_draft',claim,{'brief':source},'model',['draft.txt'])
            self.assertEqual(result['output'],'correct'); self.assertEqual(store.get(result['outputs'][0]),b'synthetic output')
            graph.close()

    def test_real_process_success_resume_and_fresh_review(self):
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'output'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            provenance=Provenance(graph); supervisor=Supervisor(str(output)); fenced=[]
            executor=CodexExecutor(supervisor,provenance,ArtifactStore(str(art)),fenced.append)
            args=dict(run='r',role='canonical_draft',claim=claim,skills={'writer':'hash'},prompt='prompt',model='synthetic',input_versions=['input'],output_paths=['draft.txt'])
            result=executor.execute(**args); self.assertEqual(result['output'],'correct'); self.assertEqual(executor.artifacts.get(result['outputs'][0]),b'synthetic output')
            graph.bind_session('r','canonical_draft',result['session_id'],claim)
            supervisor.turn='turn-two'; resumed=executor.execute(**args,session_id=result['session_id']); self.assertEqual(resumed['turn_id'],'turn-two')
            supervisor.thread='review-thread'; supervisor.turn='review-turn'
            review=executor.review(subject='subject-hash',**{**args,'role':'canonical_review','skills':{'editor':'hash'}})
            self.assertEqual(review['session_id'],'review-thread'); self.assertTrue(provenance.subject_matches('r','canonical_review','review-thread','subject-hash'))
            self.assertEqual(fenced,[]); graph.close()
    def test_failed_or_cancelled_process_fences_and_never_completes(self):
        for mode in ('invalid','approval','exit','hang','late-exit'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
                graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
                fenced=[]; executor=CodexExecutor(Supervisor(str(output),mode),Provenance(graph),ArtifactStore(str(art)),fenced.append)
                with self.assertRaises(Exception): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=[],timeout=.3)
                self.assertEqual(fenced,['r']); self.assertEqual(graph.db.execute('SELECT COUNT(*) FROM completed_turn_outputs').fetchone()[0],0); graph.close()
    def test_cancellation_and_escape_reject_output(self):
        for cancel,paths in ((True,[]),(False,['../outside'])):
            with tempfile.TemporaryDirectory() as root:
                output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
                graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
                fenced=[]; executor=CodexExecutor(Supervisor(str(output)),Provenance(graph),ArtifactStore(str(art)),fenced.append)
                with self.assertRaises(Exception): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=paths,cancelled=lambda:cancel)
                self.assertEqual(graph.db.execute('SELECT COUNT(*) FROM completed_turn_outputs').fetchone()[0],0)
                if cancel: self.assertEqual(fenced,['r'])
                graph.close()

    def test_executor_accepts_upgraded_docker_stream_and_records_provenance(self):
        import json, socketserver, struct, threading
        from runner.docker_attach import DockerAttachTransport
        from runner.docker_controller import UnixEngine
        class EngineHandler(socketserver.BaseRequestHandler):
            def handle(self):
                head=b''
                while b'\r\n\r\n' not in head: head+=self.request.recv(1024)
                self.request.sendall(b'HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/vnd.docker.raw-stream\r\n\r\n')
                reader=self.request.makefile('rb')
                def emit(message,channel=1):
                    data=json.dumps(message).encode()+b'\n'; frame=struct.pack('>BxxxI',channel,len(data))+data
                    self.request.sendall(frame[:3]); self.request.sendall(frame[3:])
                for line in reader:
                    msg=json.loads(line); method=msg.get('method'); ident=msg.get('id')
                    if method=='initialize': emit({'id':ident,'result':{}})
                    elif method=='thread/start': emit({'id':ident,'result':{'thread':{'id':'thread-one'}}})
                    elif method=='turn/start':
                        emit({'id':ident,'result':{'turn':{'id':'turn-one'}}})
                        emit({'ignored':'stderr'},2)
                        emit({'method':'item/agentMessage/delta','params':{'threadId':'thread-one','turnId':'wrong','delta':'wrong'}})
                        emit({'method':'item/agentMessage/delta','params':{'threadId':'thread-one','turnId':'turn-one','delta':'correct'}})
                        emit({'method':'turn/completed','params':{'threadId':'thread-one','turn':{'id':'turn-one','status':'completed'}}})
                        break
                reader.close()
        class Server(socketserver.UnixStreamServer): pass
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); (output/'draft.txt').write_text('synthetic output')
            art=Path(root)/'art'; art.mkdir(mode=0o700)
            sock=str(Path(root)/'engine.sock'); server=Server(sock,EngineHandler); thread=threading.Thread(target=server.serve_forever); thread.start()
            class AttachedHandle:
                output_dir=str(output)
                def open_transport(self,deadline,cancelled):
                    self.sock=UnixEngine(sock).attach('a'*64); return DockerAttachTransport(self.sock,deadline,cancelled)
                def finish(self): self.sock.close(); return True
                def cancel(self):
                    if hasattr(self,'sock'): self.sock.close()
            class AttachedSupervisor:
                def preflight(self): return True
                def start(self,run,role): return AttachedHandle()
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            try:
                store=ArtifactStore(str(art)); executor=CodexExecutor(AttachedSupervisor(),Provenance(graph),store,lambda run:None)
                result=executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=['input'],output_paths=['draft.txt'])
                self.assertEqual(result['output'],'correct'); self.assertEqual(store.get(result['outputs'][0]),b'synthetic output')
                self.assertEqual(graph.db.execute('SELECT count(*) FROM completed_turn_outputs').fetchone()[0],1)
            finally: graph.close(); server.shutdown(); thread.join(); server.server_close()

    def test_fence_before_turn_blocks_turn_and_fences_worker(self):
        class Transport:
            def __init__(self): self.sent=[]; self.responses=[{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'thread-one'}}}]
            def send(self,message): self.sent.append(message)
            def receive(self): return self.responses.pop(0)
        transport=Transport()
        class GuardedHandle:
            output_dir='unused'
            def open_transport(self,deadline,cancelled): return transport
            def assert_active(self): raise RuntimeError('fenced')
            def cancel(self): pass
        class GuardedSupervisor:
            def preflight(self): return True
            def start(self,run,role): return GuardedHandle()
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            art=Path(root)/'art'; art.mkdir(mode=0o700); fenced=[]
            executor=CodexExecutor(GuardedSupervisor(),Provenance(graph),ArtifactStore(str(art)),fenced.append)
            with self.assertRaises(Exception): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=[])
            self.assertFalse(any(item.get('method')=='turn/start' for item in transport.sent))
            self.assertEqual(fenced,['r']); graph.close()

    def test_resume_requires_explicit_persistent_session_capability_before_launch(self):
        class NoPersistence:
            started=False
            def preflight(self): return True
            def start(self,run,role): self.started=True; raise AssertionError('must not launch')
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            art=Path(root)/'art'; art.mkdir(mode=0o700); supervisor=NoPersistence()
            executor=CodexExecutor(supervisor,Provenance(graph),ArtifactStore(str(art)),lambda run:None)
            with self.assertRaisesRegex(Exception,'persistent session'):
                executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=[],session_id='thread-one')
            self.assertFalse(supervisor.started); graph.close()

    def test_executor_passes_exact_session_context_and_binds_completed_thread(self):
        class ContextSupervisor(Supervisor):
            def __init__(self,root): super().__init__(root); self.context=None; self.bound=[]
            def start_execution(self,run,role,session,skills,model):
                self.context=(run,role,session,skills,model); handle=self.start(run,role)
                handle.bind_session=self.bound.append
                return handle
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            supervisor=ContextSupervisor(str(output)); executor=CodexExecutor(supervisor,Provenance(graph),ArtifactStore(str(art)),lambda _:None)
            result=executor.execute(run='r',role='canonical_draft',claim=claim,skills={'writer':'hash'},prompt='p',model='m',input_versions=[],output_paths=['draft.txt'])
            self.assertEqual(supervisor.context,('r','canonical_draft',None,{'writer':'hash'},'m')); self.assertEqual(supervisor.bound,[result['session_id']])
            graph.close()

    def test_independent_review_can_return_valid_blocking_correction(self):
        class BlockedSupervisor(Supervisor):
            def start(self,run,role):
                handle=super().start(run,role)
                return handle
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            supervisor=BlockedSupervisor(str(output),thread='review-thread'); executor=CodexExecutor(supervisor,Provenance(graph),ArtifactStore(str(art)),lambda _:None)
            import os
            prior=os.environ.get('FAKE_BLOCK_REVIEW'); os.environ['FAKE_BLOCK_REVIEW']='1'
            try:
                result=executor.review(subject='subject-hash',run='r',role='canonical_review',claim=claim,skills={},prompt='p',model='m',input_versions=['input'],output_paths=['draft.txt'])
                self.assertEqual(result['review_status'],'blocked'); self.assertTrue(result['evidence_ref'])
            finally:
                if prior is None: del os.environ['FAKE_BLOCK_REVIEW']
                else: os.environ['FAKE_BLOCK_REVIEW']=prior
                graph.close()

    def test_review_rejects_finding_without_bound_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            supervisor=Supervisor(str(output),thread='review-thread'); executor=CodexExecutor(supervisor,Provenance(graph),ArtifactStore(str(art)),lambda _:None)
            prior=os.environ.get('FAKE_BAD_REVIEW'); os.environ['FAKE_BAD_REVIEW']='1'
            try:
                with self.assertRaisesRegex(Exception,'finding evidence'): executor.review(subject='subject-hash',run='r',role='canonical_review',claim=claim,skills={},prompt='p',model='m',input_versions=['input'],output_paths=['draft.txt'])
            finally:
                if prior is None: del os.environ['FAKE_BAD_REVIEW']
                else: os.environ['FAKE_BAD_REVIEW']=prior
                graph.close()
