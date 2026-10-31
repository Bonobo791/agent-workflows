import tempfile
import unittest
from runner.codex_session import run_codex
from runner.graph import GraphStore, engineering_definition
from runner.provenance import Provenance

class FakeTransport:
    def __init__(self): self.messages=iter([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'actual-thread'}}},{'id':3,'result':{'turn':{'id':'turn'}}},{'method':'item/agentMessage/delta','params':{'threadId':'actual-thread','turnId':'turn','delta':'synthetic output'}},{'method':'turn/completed','params':{'threadId':'actual-thread','turn':{'id':'turn','status':'completed'}}}])
    def send(self,message): pass
    def receive(self): return next(self.messages)

class CodexSessionTests(unittest.TestCase):
    def test_only_final_agent_message_becomes_structured_output(self):
        class Messages(FakeTransport):
            def __init__(self):
                self.messages=iter([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'t'}}},{'id':3,'result':{'turn':{'id':'turn'}}},
                    {'method':'item/agentMessage/delta','params':{'threadId':'t','turnId':'turn','itemId':'comment','delta':'Checking first.'}},
                    {'method':'item/completed','params':{'threadId':'t','turnId':'turn','item':{'type':'agentMessage','id':'comment','phase':'commentary','text':'Checking first.'}}},
                    {'method':'item/agentMessage/delta','params':{'threadId':'t','turnId':'turn','itemId':'answer','delta':'{"status":"passed"}'}},
                    {'method':'item/completed','params':{'threadId':'t','turnId':'turn','item':{'type':'agentMessage','id':'answer','phase':'final_answer','text':'{"status":"passed"}'}}},
                    {'method':'turn/completed','params':{'threadId':'t','turn':{'id':'turn','status':'completed'}}}])
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db');graph.register(engineering_definition());graph.create('r','engineering');claim=graph.claim('r',60)
            try:self.assertEqual(run_codex(Messages(),Provenance(graph),'r','qa',claim,{},'prompt','model')['output'],'{"status":"passed"}')
            finally:graph.close()
    def test_legacy_unknown_phase_uses_last_completed_message(self):
        class Messages(FakeTransport):
            def __init__(self):
                self.messages=iter([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'t'}}},{'id':3,'result':{'turn':{'id':'turn'}}},
                    {'method':'item/completed','params':{'threadId':'t','turnId':'turn','item':{'type':'agentMessage','id':'one','text':'earlier'}}},
                    {'method':'item/completed','params':{'threadId':'t','turnId':'turn','item':{'type':'agentMessage','id':'two','text':'final'}}},
                    {'method':'turn/completed','params':{'threadId':'t','turn':{'id':'turn','status':'completed'}}}])
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db');graph.register(engineering_definition());graph.create('r','engineering');claim=graph.claim('r',60)
            try:self.assertEqual(run_codex(Messages(),Provenance(graph),'r','qa',claim,{},'prompt','model')['output'],'final')
            finally:graph.close()
    def test_only_recorded_writer_with_matching_skills_can_resume(self):
        from runner.graph import marketing_definition
        from runner.protocol import ProtocolError
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
            provenance=Provenance(graph)
            first=run_codex(FakeTransport(),provenance,'r','canonical_draft',claim,{'writer':'hash'},'prompt','model')
            graph.bind_session('r','canonical_draft',first['session_id'],claim)
            resumed=run_codex(FakeTransport(),provenance,'r','canonical_draft',claim,{'writer':'hash'},'revision','model',session_id=first['session_id'])
            self.assertEqual(resumed['turn_id'],'turn')
            with self.assertRaises(ProtocolError): run_codex(FakeTransport(),provenance,'r','canonical_draft',claim,{'writer':'changed'},'prompt','model',session_id=first['session_id'])
            with self.assertRaises(ProtocolError): run_codex(FakeTransport(),provenance,'r','canonical_review',claim,{},'prompt','model',session_id=first['session_id'])
            graph.close()

    def test_binds_actual_thread_and_skill_hashes(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(engineering_definition()); graph.create('r','engineering'); claim=graph.claim('r',60)
            provenance=Provenance(graph)
            result=run_codex(FakeTransport(),provenance,'r','qa',claim,{'editor':'abc'},'synthetic prompt','model')
            self.assertEqual(result['session_id'],'actual-thread')
            self.assertTrue(provenance.verify('r','qa','actual-thread',{'editor':'abc'}))
            self.assertEqual(result['output'],'synthetic output')
            graph.close()

    def test_deltas_are_filtered_to_current_turn(self):
        class Interleaved(FakeTransport):
            def __init__(self):
                self.messages=iter([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'t'}}},
                    {'method':'item/agentMessage/delta','params':{'threadId':'t','turnId':'old','delta':'stale'}},
                    {'method':'item/agentMessage/delta','params':{'threadId':'t','turnId':'new','delta':'current'}},
                    {'id':3,'result':{'turn':{'id':'new'}}},
                    {'method':'turn/completed','params':{'threadId':'t','turn':{'id':'new','status':'completed'}}}])
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(engineering_definition()); graph.create('r','engineering'); claim=graph.claim('r',60)
            result=run_codex(Interleaved(),Provenance(graph),'r','qa',claim,{},'prompt','model')
            self.assertEqual(result['output'],'current'); graph.close()

    def test_tool_completion_is_scoped_to_current_thread_and_turn(self):
        class Interleaved(FakeTransport):
            def __init__(self):
                self.messages=iter([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'t'}}},
                    {'method':'item/completed','params':{'threadId':'t','turnId':'old','item':{'type':'webSearch','id':'old'}}},
                    {'id':3,'result':{'turn':{'id':'new'}}},
                    {'method':'item/completed','params':{'threadId':'t','turnId':'new','item':{'type':'webSearch','id':'current','query':'fixture'}}},
                    {'method':'item/completed','params':{'threadId':'other','turnId':'new','item':{'type':'webSearch','id':'other'}}},
                    {'method':'turn/completed','params':{'threadId':'t','turn':{'id':'new','status':'completed'}}}])
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(engineering_definition()); graph.create('r','engineering'); claim=graph.claim('r',60)
            result=run_codex(Interleaved(),Provenance(graph),'r','qa',claim,{},'prompt','model')
            self.assertEqual([item['id'] for item in result['tool_events']],['current']); graph.close()

    def test_early_delta_byte_limit(self):
        from runner.protocol import ProtocolError
        class Oversized(FakeTransport):
            def __init__(self): self.messages=iter([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'t'}}}, {'method':'item/agentMessage/delta','params':{'threadId':'t','turnId':'old','delta':'x'*(1048576+1)}}])
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(engineering_definition()); graph.create('r','engineering'); claim=graph.claim('r',60)
            with self.assertRaises(ProtocolError): run_codex(Oversized(),Provenance(graph),'r','qa',claim,{},'prompt','model')
            graph.close()
