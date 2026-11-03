import os
from contextlib import closing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from runner.artifacts import ArtifactStore
from runner.codex_executor import CodexExecutor
from runner.graph import GraphStore, marketing_definition
from runner.provenance import Provenance
from runner.stdio import StdioError

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





    def test_images_require_a_subject_bound_local_asset_review(self):
        from runner.codex_executor import ExecutorError
        from tests.test_assets import png
        with tempfile.TemporaryDirectory() as root:
            art=Path(root)/'art';art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db');graph.register(marketing_definition());graph.create('r','marketing');claim=graph.claim('r',60)
            store=ArtifactStore(str(art));ref=store.put(png())
            executor=CodexExecutor(Supervisor(root),Provenance(graph),store,lambda run:None)
            try:
                with self.assertRaises(ExecutorError): executor.execute(run='r',role='asset_review',claim=claim,skills={},prompt='x',model='m',input_versions=[ref],output_paths=[],review_subject='subject',image_refs=[ref])
                self.assertEqual(graph.db.execute('SELECT count(*) FROM execution_image_inputs').fetchone()[0],0)
            finally:graph.close()
    def test_local_output_rewrite_preserves_untrusted_payload(self):
        from runner.codex_executor import _local_prompt, ExecutorError
        prompt='Write /output/result.txt and /output/sources.json.\n<workflow-input-json>\n{"text":"/output/untrusted"}\nReview subject: x'
        self.assertEqual(_local_prompt(prompt,'/workspace/private',['result.txt'],prompt.index('\n<workflow-input-json>')), 'Write /workspace/private/result.txt and /workspace/private/sources.json.\n<workflow-input-json>\n{"text":"/output/untrusted"}\nReview subject: x')
        with self.assertRaises(ExecutorError): _local_prompt('missing instructions\n{}','/workspace/private',['result.txt'],len('missing instructions'))
    def test_supervisor_config_guard_blocks_thread_and_fences(self):
        class GuardedHandle(Handle):
            def verify_config(self,request): return False
        class GuardedSupervisor(Supervisor):
            def start(self,run,role): return GuardedHandle(self.root,self.mode,self.thread,self.turn,role)
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            fenced=[]; executor=CodexExecutor(GuardedSupervisor(str(output)),Provenance(graph),ArtifactStore(str(art)),fenced.append)
            try:
                with self.assertRaises(Exception): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=[])
                self.assertEqual(fenced,['r'])
                self.assertEqual(graph.db.execute('SELECT count(*) FROM graph_sessions').fetchone()[0],0)
            finally: graph.close()


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


    def test_tool_audit_is_redacted_and_failure_rolls_back_completion(self):
        import sqlite3
        for failure in (False,True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
                graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
                provenance=Provenance(graph); executor=CodexExecutor(Supervisor(str(output),mode='tool'),provenance,ArtifactStore(str(art)),lambda _:None)
                try:
                    if failure: graph.db.execute("CREATE TRIGGER reject_tool_audit BEFORE INSERT ON tool_audit BEGIN SELECT RAISE(ABORT,'failure'); END")
                    args=dict(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=['draft.txt'])
                    if failure:
                        with self.assertRaises(sqlite3.IntegrityError): executor.execute(**args)
                        self.assertFalse(provenance.completed('r','canonical_draft','thread-one'))
                        self.assertEqual(graph.db.execute('SELECT COUNT(*) FROM execution_turn_audit').fetchone()[0],0)
                    else:
                        result=executor.execute(**args)
                        row=graph.db.execute('SELECT tool_kind,status,input_present,output_present,evidence_ref FROM tool_audit').fetchone()
                        self.assertEqual(row[:4],('mcpToolCall','completed',1,1))
                        import json
                        receipt=json.loads(executor.artifacts.get(row[4]))
                        self.assertEqual(receipt['items'],result['tool_events'])
                        self.assertNotIn('synthetic-private',str(row))
                finally: graph.close()
    def test_command_notification_is_observed_without_claiming_command_success(self):
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            fenced=[]; executor=CodexExecutor(Supervisor(str(output),mode='command_exit'),Provenance(graph),ArtifactStore(str(art)),fenced.append)
            try:
                with self.assertRaises(Exception): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=[])
                row=graph.db.execute('SELECT tool_kind,status,input_present,output_present,evidence_ref FROM tool_audit').fetchone()
                self.assertEqual(row[:4],('commandExecution','observed',1,1))
                self.assertNotIn('synthetic-private',str(row))
                self.assertEqual(fenced,['r'])
            finally: graph.close()
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
