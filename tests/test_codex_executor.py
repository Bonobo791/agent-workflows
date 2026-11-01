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
    def test_interrupted_local_turn_keeps_scope_link_and_fences_session(self):
        from unittest.mock import patch
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import LocalScopes
        from runner.process_transport import ProcessTransport
        from tests.test_local_scope import Backend
        class ScopedHandle(Handle):
            def __init__(self,root,unit):
                super().__init__(root,'turn_exit','local-thread','local-turn','canonical_draft')
                self.unit=unit; self.output_path_prefix=root
            def assert_active(self): return True
            def verify_config(self,request): return True
            def open_transport(self,deadline,cancelled): return ProcessTransport(self.process,deadline,cancelled)
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(mode=0o700); art=Path(root)/'art'; art.mkdir(mode=0o700)
            binary=Path(root)/'codex'; binary.write_text('x'); binary.chmod(0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            backend=Backend(); supervisor=LocalSupervisor(root+'/db',str(binary),{'HOME':root},backend,lambda run:True,lambda run,role:str(output),lambda:True,{'mcp_servers':[],'plugins':[]})
            scopes=LocalScopes(root+'/db',backend); unit=scopes.reserve('r'); backend.unit=unit; scopes.bind('r',unit); scopes.close()
            handle=ScopedHandle(str(output),unit); fenced=[]; executor=CodexExecutor(supervisor,Provenance(graph),ArtifactStore(str(art)),fenced.append)
            try:
                with patch.object(supervisor,'preflight',return_value=True), patch('runner.local_supervisor.start_local_process',return_value=handle):
                    with self.assertRaises(Exception): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='instructions\n{}',instruction_length=len('instructions'),model='m',input_versions=[],output_paths=[])
                row=graph.db.execute('SELECT e.session,e.unit,l.turn,l.status FROM local_execution_scopes e JOIN execution_link_audit l ON l.execution=e.execution').fetchone()
                self.assertEqual(row,('local-thread',unit,'local-turn','observed'))
                self.assertEqual(graph.db.execute('SELECT state FROM local_sessions').fetchone(),('active',))
                self.assertFalse(supervisor.can_resume('r','canonical_draft','local-thread',{},'m'))
                self.assertEqual(fenced,['r'])
            finally: graph.close()

    def test_session_binding_failure_prevents_turn_and_fences(self):
        class UnboundHandle(Handle):
            def bind_session(self,session): raise RuntimeError('binding failed')
        class UnboundSupervisor(Supervisor):
            def start(self,run,role): return UnboundHandle(self.root,self.mode,self.thread,self.turn,role)
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            fenced=[]; executor=CodexExecutor(UnboundSupervisor(str(output)),Provenance(graph),ArtifactStore(str(art)),fenced.append)
            try:
                with self.assertRaises(RuntimeError): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=[])
                self.assertFalse((output/'draft.txt').exists())
                self.assertEqual(graph.db.execute('SELECT count(*) FROM execution_link_audit').fetchone()[0],0)
                self.assertEqual(fenced,['r'])
            finally: graph.close()

    def test_turn_identity_survives_exit_without_tool_events(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            fenced=[]; executor=CodexExecutor(Supervisor(str(output),mode='turn_exit'),Provenance(graph),ArtifactStore(str(art)),fenced.append)
            try:
                with self.assertRaises(Exception): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='private prompt',model='m',input_versions=[],output_paths=[])
                self.assertEqual(graph.db.execute('SELECT session,turn,status FROM execution_link_audit').fetchall(),[('thread-one','turn-one','observed')])
                self.assertFalse(executor.provenance.completed('r','canonical_draft','thread-one'))
                self.assertEqual(fenced,['r'])
            finally: graph.close()
            with sqlite3.connect(root+'/db') as db:
                self.assertEqual(db.execute('SELECT session,turn,status FROM execution_link_audit').fetchall(),[('thread-one','turn-one','observed')])

    def test_local_turn_requires_and_records_terminated_scope(self):
        from unittest.mock import patch
        from runner.local_supervisor import LocalSupervisor
        from runner.local_scope import LocalScopes
        from tests.test_local_scope import Backend
        class ScopedHandle(Handle):
            def __init__(self,root,db,backend,unit,corrupt=False):
                super().__init__(root,'ok','other-thread' if corrupt else 'local-thread','local-turn','asset_production')
                self.db,self.backend,self.unit=db,backend,unit
                self.corrupt=corrupt
                self.output_path_prefix=root
            def assert_active(self): return True
            def verify_config(self,request): return True
            def open_transport(self,deadline,cancelled):
                from runner.process_transport import ProcessTransport
                return ProcessTransport(self.process,deadline,cancelled)
            def finish(self):
                if not super().finish(): return False
                scopes=LocalScopes(self.db,self.backend)
                try:
                    stopped=scopes.stop('r',self.unit)
                    if self.corrupt:scopes.db.execute('UPDATE local_scopes SET invocation=NULL WHERE unit=?',(self.unit,))
                    return stopped
                finally: scopes.close()
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(mode=0o700); art=Path(root)/'art'; art.mkdir(mode=0o700)
            binary=Path(root)/'codex'; binary.write_text('x'); binary.chmod(0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            backend=Backend(); supervisor=LocalSupervisor(root+'/db',str(binary),{'HOME':root},backend,lambda run:True,lambda run,role:str(output),lambda:True,{'mcp_servers':[],'plugins':[]})
            scopes=LocalScopes(root+'/db',backend); unit=scopes.reserve('r'); backend.unit=unit; scopes.bind('r',unit); scopes.close()
            handle=ScopedHandle(str(output),root+'/db',backend,unit)
            store=ArtifactStore(str(art));fenced=[];executor=CodexExecutor(supervisor,Provenance(graph),store,fenced.append)
            try:
                with patch.object(supervisor,'preflight',return_value=True), patch('runner.local_supervisor.start_local_process',return_value=handle):
                    result=executor.execute(run='r',role='asset_production',claim=claim,skills={},prompt='instructions\n{}',instruction_length=len('instructions'),model='m',input_versions=[],output_paths=[])
                row=graph.db.execute('SELECT unit,binding FROM local_turn_scopes WHERE run=? AND role=? AND session=? AND turn=?',('r','asset_production',result['session_id'],result['turn_id'])).fetchone()
                self.assertEqual(row,(unit,supervisor._binding('r','asset_production',{},'m')[1]))
                linked=graph.db.execute('SELECT unit,binding FROM local_execution_scopes WHERE run=? AND role=? AND session=?',('r','asset_production',result['session_id'])).fetchone()
                self.assertEqual(linked,row)
                self.assertEqual(graph.db.execute('SELECT state FROM local_scopes WHERE unit=?',(unit,)).fetchone(),('terminated',))
                scopes=LocalScopes(root+'/db',backend);unit2=scopes.reserve('r');backend.unit=unit2;backend.state='active';scopes.bind('r',unit2);scopes.close()
                broken=ScopedHandle(str(output),root+'/db',backend,unit2,corrupt=True)
                from runner.codex_executor import ExecutorError
                with patch.object(supervisor,'preflight',return_value=True), patch('runner.local_supervisor.start_local_process',return_value=broken):
                    with self.assertRaises(ExecutorError):executor.execute(run='r',role='asset_production',claim=claim,skills={},prompt='instructions\n{}',instruction_length=len('instructions'),model='m',input_versions=[],output_paths=[])
                self.assertIsNone(graph.db.execute("SELECT 1 FROM local_turn_scopes WHERE session='other-thread'").fetchone())
                self.assertEqual(fenced,['r'])
                self.assertEqual(graph.db.execute('SELECT status FROM execution_launch_audit ORDER BY id DESC LIMIT 1').fetchone(),('uncertain',))
            finally: graph.close()
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
                self.assertEqual(graph.db.execute('SELECT status FROM execution_launch_audit ORDER BY id').fetchall(),[('requested',),('uncertain',)])
                from runner.audit_export import read_events
                events,_=read_events(graph.db,'execution_launch_audit',0,10,b'x'*32)
                self.assertEqual(events[0]['execution_id'],events[1]['execution_id'])
                self.assertEqual([event['status'] for event in events],['requested','uncertain'])
            finally: release.set(); graph.close()
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(root+'/db')) as reopened:
                self.assertEqual(reopened.execute('SELECT status FROM execution_launch_audit ORDER BY id').fetchall(),[('requested',),('uncertain',)])

    def test_launch_audit_failure_blocks_supervisor_start(self):
        import sqlite3
        class NeverStart:
            def preflight(self): return True
            def start(self,*args): raise AssertionError('must not start')
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            executor=CodexExecutor(NeverStart(),Provenance(graph),ArtifactStore(root),lambda _:None)
            try:
                graph.db.execute("CREATE TRIGGER reject_launch_audit BEFORE INSERT ON execution_launch_audit BEGIN SELECT RAISE(ABORT,'failure'); END")
                with self.assertRaises(sqlite3.IntegrityError): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=[])
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

    def test_real_process_success_resume_and_fresh_review(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'output'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            provenance=Provenance(graph); supervisor=Supervisor(str(output)); fenced=[]
            executor=CodexExecutor(supervisor,provenance,ArtifactStore(str(art)),fenced.append)
            args=dict(run='r',role='canonical_draft',claim=claim,skills={'writer':'hash'},prompt='prompt',model='synthetic',input_versions=['input'],output_paths=['draft.txt'])
            result=executor.execute(**args); self.assertEqual(result['output'],'correct'); self.assertEqual(executor.artifacts.get(result['outputs'][0]),b'synthetic output')
            self.assertEqual(graph.db.execute('SELECT status FROM execution_launch_audit ORDER BY id').fetchall(),[('requested',),('completed',)])
            launch=graph.db.execute("SELECT execution FROM execution_launch_audit WHERE status='completed'").fetchone()[0]
            self.assertEqual(graph.db.execute('SELECT execution,session,turn,status FROM execution_link_audit ORDER BY id').fetchall(),[(launch,result['session_id'],result['turn_id'],status) for status in ('observed','completed')])
            audit=graph.db.execute('SELECT model,input_versions,output_refs,response_ref,tool_evidence_ref FROM execution_turn_audit WHERE run=? AND session=? AND turn=?',('r',result['session_id'],result['turn_id'])).fetchone()
            self.assertEqual(audit,('synthetic',json.dumps(['input']),json.dumps(result['outputs']),result['response_ref'],result['tool_evidence_ref']))
            graph.bind_session('r','canonical_draft',result['session_id'],claim)
            supervisor.turn='turn-two'; resumed=executor.execute(**args,session_id=result['session_id']); self.assertEqual(resumed['turn_id'],'turn-two')
            supervisor.thread='review-thread'; supervisor.turn='review-turn'
            review=executor.review(subject='subject-hash',**{**args,'role':'canonical_review','skills':{'editor':'hash'}})
            self.assertEqual(review['session_id'],'review-thread'); self.assertTrue(provenance.subject_matches('r','canonical_review','review-thread','subject-hash'))
            self.assertEqual(fenced,[]); graph.close()

    def test_turn_audit_failure_rolls_back_completion(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            provenance=Provenance(graph); executor=CodexExecutor(Supervisor(str(output)),provenance,ArtifactStore(str(art)),lambda _:None)
            graph.db.execute("CREATE TRIGGER reject_turn_audit BEFORE INSERT ON execution_turn_audit BEGIN SELECT RAISE(ABORT,'failure'); END")
            with self.assertRaises(sqlite3.IntegrityError): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=['draft.txt'])
            self.assertEqual(graph.db.execute('SELECT COUNT(*) FROM completed_turn_outputs').fetchone()[0],0)
            self.assertEqual(graph.db.execute('SELECT status FROM execution_link_audit').fetchall(),[('observed',)])
            self.assertFalse(provenance.completed('r','canonical_draft','thread-one')); graph.close()
    def test_link_audit_failure_rolls_back_completion(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            provenance=Provenance(graph); executor=CodexExecutor(Supervisor(str(output)),provenance,ArtifactStore(str(art)),lambda _:None)
            try:
                graph.db.execute("CREATE TRIGGER reject_link_audit BEFORE INSERT ON execution_link_audit BEGIN SELECT RAISE(ABORT,'failure'); END")
                with self.assertRaises(sqlite3.IntegrityError): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=['draft.txt'])
                self.assertEqual(graph.db.execute('SELECT COUNT(*) FROM execution_turn_audit').fetchone()[0],0)
                self.assertFalse(provenance.completed('r','canonical_draft','thread-one'))
            finally: graph.close()
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
    def test_observed_tool_call_survives_interrupted_turn(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); art=Path(root)/'art'; art.mkdir(mode=0o700)
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            provenance=Provenance(graph); fenced=[]; executor=CodexExecutor(Supervisor(str(output),mode='tool_exit'),provenance,ArtifactStore(str(art)),fenced.append)
            try:
                with self.assertRaises(Exception): executor.execute(run='r',role='canonical_draft',claim=claim,skills={},prompt='p',model='m',input_versions=[],output_paths=[])
                row=graph.db.execute('SELECT status,evidence_ref FROM tool_audit').fetchone()
                self.assertEqual(row[0],'completed')
                self.assertEqual(json.loads(executor.artifacts.get(row[1]))['items'][0]['type'],'mcpToolCall')
                self.assertEqual(graph.db.execute('SELECT session,turn,status FROM execution_link_audit').fetchone(),('thread-one','turn-one','observed'))
                self.assertFalse(provenance.completed('r','canonical_draft','thread-one'))
                self.assertEqual(fenced,['r'])
            finally: graph.close()
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(root+'/db')) as reopened:
                self.assertEqual(reopened.execute('SELECT status FROM tool_audit').fetchall(),[('completed',)])
                self.assertEqual(reopened.execute('SELECT status FROM execution_link_audit').fetchall(),[('observed',)])
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
