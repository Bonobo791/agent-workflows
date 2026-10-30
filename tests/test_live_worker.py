import contextlib
import json
import tempfile
import unittest
from runner.graph import GraphStore, marketing_definition
from runner.artifacts import ArtifactStore
from runner.provenance import Provenance
from runner.live_worker import LiveWorker, VerifiedVerdict

class FakeTransport:
    def __init__(self,output_ref='result'):
        output=json.dumps({'stage':'intake','status':'passed','input_versions':['source'],'outputs':[output_ref],'findings':[]})
        self.messages=iter([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'thread'}}},{'id':3,'result':{'turn':{'id':'turn'}}},{'method':'item/agentMessage/delta','params':{'threadId':'thread','turnId':'turn','delta':output}},{'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}}])
    def send(self,message): pass
    def receive(self): return next(self.messages)
class FakeRuntime:
    def __init__(self,output_ref='result'): self.output_ref=output_ref
    @contextlib.contextmanager
    def open_session(self): yield FakeTransport(self.output_ref)


def verifier(store,provenance,token):
    def gate(stage,result):
        if not provenance.verify('r','independent_review','independent-reviewer',{}):
            provenance.bind('r','independent_review','independent-reviewer',{},token,subject=result['_subject_hash'])
            review_artifact=store.put(b'independent review artifact')
            review=json.dumps({'stage':'independent_review','status':'passed','input_versions':[result['_subject_hash']],'outputs':[review_artifact],'findings':[]},sort_keys=True)
            review_ref=store.put(review.encode())
            provenance.mark_completed('r','independent_review','independent-reviewer',token,review_ref)
        else: review_ref=provenance.completion_hash('r','independent_review','independent-reviewer')
        proof={'review_output_ref':review_ref,'run':'r','stage':stage,'subject_hash':result['_subject_hash'],'reviewer_session':'independent-reviewer','reviewer_role':'independent_review','skills':{}}
        ref=store.put(json.dumps(proof,sort_keys=True).encode())
        return VerifiedVerdict(True,(ref,))
    return gate

class LiveWorkerTests(unittest.TestCase):
    def test_protocol_output_uses_authoritative_session(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60)
            provenance=Provenance(graph); store=ArtifactStore(root)
            worker=LiveWorker(graph,store,provenance,FakeRuntime(store.put(b'output')),verifier(store,provenance,token))
            ref=worker.execute('r','intake',token,{'task':'synthetic','input_versions':['source']},'prompt','model')
            self.assertTrue(provenance.verify('r','intake','thread',{}))
            self.assertEqual(graph.get('r','intake')['version'],ref)
            graph.close()

    def test_engineering_stage_uses_injected_independent_gate(self):
        from runner.graph import engineering_definition
        class EngineeringTransport(FakeTransport):
            def __init__(self,output_ref):
                output=json.dumps({'stage':'intake','status':'passed','input_versions':['source'],'outputs':[output_ref],'findings':[]})
                self.messages=iter([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'eng-thread'}}},{'id':3,'result':{'turn':{'id':'turn'}}},{'method':'item/agentMessage/delta','params':{'threadId':'eng-thread','turnId':'turn','delta':output}},{'method':'turn/completed','params':{'threadId':'eng-thread','turn':{'id':'turn','status':'completed'}}}])
        class Runtime:
            def __init__(self,ref): self.ref=ref
            @contextlib.contextmanager
            def open_session(self): yield EngineeringTransport(self.ref)
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(engineering_definition()); graph.create('r','engineering'); token=graph.claim('r',60)
            store=ArtifactStore(root); provenance=Provenance(graph)
            worker=LiveWorker(graph,store,provenance,Runtime(store.put(b'output')),verifier(store,provenance,token))
            worker.execute('r','intake',token,{'task':'synthetic','input_versions':['source']},'prompt','model')
            self.assertEqual(graph.get('r','intake')['status'],'passed'); graph.close()

    def test_awaiting_review_never_becomes_passed(self):
        class ReviewTransport(FakeTransport):
            def __init__(self):
                output=json.dumps({'stage':'intake','status':'awaiting_review','input_versions':['source'],'outputs':[],'findings':[]})
                self.messages=iter([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'thread'}}},{'id':3,'result':{'turn':{'id':'turn'}}},{'method':'item/agentMessage/delta','params':{'threadId':'thread','turnId':'turn','delta':output}},{'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}}])
        class Runtime:
            @contextlib.contextmanager
            def open_session(self): yield ReviewTransport()
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60)
            store=ArtifactStore(root); evidence=store.put(b'evidence')
            worker=LiveWorker(graph,store,Provenance(graph),Runtime(),lambda stage,result: VerifiedVerdict(True,(evidence,)))
            worker.execute('r','intake',token,{'input_versions':['source']},'prompt','model')
            self.assertEqual(graph.get('r','intake')['status'],'awaiting_review'); graph.close()

    def test_unrelated_existing_artifact_cannot_authorize_pass(self):
        from runner.graph_runner import ExecutionError
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60)
            store=ArtifactStore(root); unrelated=store.put(b'unrelated')
            worker=LiveWorker(graph,store,Provenance(graph),FakeRuntime(store.put(b'output')),lambda stage,result:VerifiedVerdict(True,(unrelated,)))
            with self.assertRaises(ExecutionError): worker.execute('r','intake',token,{'input_versions':['source']},'prompt','model')
            self.assertIsNone(graph.get('r','intake')); graph.close()

    def test_model_or_prompt_change_is_not_cached(self):
        from runner.graph_runner import ExecutionError
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60)
            store=ArtifactStore(root); provenance=Provenance(graph)
            worker=LiveWorker(graph,store,provenance,FakeRuntime(store.put(b'output')),verifier(store,provenance,token))
            worker.execute('r','intake',token,{'input_versions':['source']},'first','model-a')
            # The synthetic runtime reuses a thread ID, which must fail on a fresh execution.
            with self.assertRaises(ExecutionError): worker.execute('r','intake',token,{'input_versions':['source']},'changed','model-b')
            self.assertIsNone(graph.get('r','intake')); graph.close()

    def test_reviewer_cannot_omit_required_skills(self):
        from runner.graph_runner import ExecutionError
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60)
            store=ArtifactStore(root); provenance=Provenance(graph)
            worker=LiveWorker(graph,store,provenance,FakeRuntime(store.put(b'output')),verifier(store,provenance,token),required_skills={'independent_review':['required-skill']})
            with self.assertRaises(ExecutionError): worker.execute('r','intake',token,{'input_versions':['source']},'prompt','model')
            self.assertIsNone(graph.get('r','intake')); graph.close()

    def test_missing_reviewer_output_artifact_invalidates_cache(self):
        import os
        from runner.graph_runner import ExecutionError
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60)
            store=ArtifactStore(root); provenance=Provenance(graph)
            worker=LiveWorker(graph,store,provenance,FakeRuntime(store.put(b'output')),verifier(store,provenance,token))
            result_ref=worker.execute('r','intake',token,{'input_versions':['source']},'prompt','model')
            result=json.loads(store.get(result_ref)); proof=json.loads(store.get(result['_verified_evidence'][0])); review=json.loads(store.get(proof['review_output_ref']))
            path=root+'/'+review['outputs'][0]; os.chmod(path,0o600); os.unlink(path)
            with self.assertRaises(ExecutionError): worker.execute('r','intake',token,{'input_versions':['source']},'prompt','model')
            self.assertIsNone(graph.get('r','intake')); graph.close()
