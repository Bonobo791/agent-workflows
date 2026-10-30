import unittest
from runner.protocol import AppServer, ProtocolError

class FakeTransport:
    def __init__(self, messages): self.messages=iter(messages); self.sent=[]
    def send(self, message): self.sent.append(message)
    def receive(self): return next(self.messages)

class ProtocolTests(unittest.TestCase):
    def test_turn_saves_thread_and_requires_completed(self):
        t=FakeTransport([{'id':1,'result':{}}, {'id':2,'result':{'thread':{'id':'thread-1'}}}, {'id':3,'result':{'turn':{'id':'turn-1'}}}, {'method':'turn/completed','params':{'threadId':'thread-1','turn':{'id':'turn-1','status':'completed'}}}])
        client=AppServer(t)
        self.assertEqual(client.run('hello','example-model'), 'thread-1')
        self.assertEqual([m['method'] for m in t.sent], ['initialize','initialized','thread/start','turn/start'])
    def test_rejects_approval_request_and_failed_turn(self):
        t=FakeTransport([{'id':1,'result':{}}, {'id':2,'result':{'thread':{'id':'t'}}}, {'id':3,'result':{'turn':{'id':'x'}}}, {'id':99,'method':'item/commandExecution/requestApproval','params':{}}, {'method':'turn/completed','params':{'threadId':'t','turn':{'id':'x','status':'failed'}}}])
        with self.assertRaises(ProtocolError): AppServer(t).run('hello','model')
        self.assertIn('error', t.sent[-1])

    def test_interleaved_turn_events_are_not_lost(self):
        t=FakeTransport([{'id':1,'result':{}}, {'id':2,'result':{'thread':{'id':'t'}}}, {'method':'item/agentMessage/delta','params':{'threadId':'t','delta':'hello'}}, {'id':3,'result':{'turn':{'id':'x'}}}, {'method':'turn/completed','params':{'threadId':'t','turn':{'id':'x','status':'completed'}}}])
        events=[]; threads=[]
        AppServer(t,on_event=events.append,on_thread=threads.append).run('hello','model')
        self.assertEqual(threads,['t'])
        self.assertEqual(events[0]['method'],'item/agentMessage/delta')

    def test_usage_limit_is_distinct_from_failure(self):
        from runner.protocol import RateLimited
        t=FakeTransport([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'t'}}},{'id':3,'result':{'turn':{'id':'x'}}}, {'method':'turn/completed','params':{'threadId':'t','turn':{'id':'x','status':'failed','error':{'codexErrorInfo':'UsageLimitExceeded'}}}}])
        with self.assertRaises(RateLimited): AppServer(t).run('prompt','model')

class BeforeTurnFenceTests(unittest.TestCase):
    def test_turn_is_not_sent_after_fence_fails(self):
        from runner.protocol import AppServer, ProtocolError
        class Transport:
            def __init__(self): self.sent=[]; self.responses=[{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'t'}}}]
            def send(self,message): self.sent.append(message)
            def receive(self): return self.responses.pop(0)
        transport=Transport()
        def fence(): raise ProtocolError('cancelled')
        with self.assertRaises(ProtocolError): AppServer(transport,before_turn=fence).run('p','m')
        self.assertFalse(any(message.get('method')=='turn/start' for message in transport.sent))

class ModelEffortTests(unittest.TestCase):
    def test_turn_sends_discovered_model_and_medium_effort(self):
        from runner.protocol import AppServer
        class Transport:
            def __init__(self): self.sent=[]; self.responses=[{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'t'}}},{'id':3,'result':{'turn':{'id':'turn'}}},{'method':'turn/completed','params':{'threadId':'t','turn':{'id':'turn','status':'completed'}}}]
            def send(self,message): self.sent.append(message)
            def receive(self): return self.responses.pop(0)
        transport=Transport(); AppServer(transport).run('p','provider-terra',effort='medium')
        turn=next(x for x in transport.sent if x.get('method')=='turn/start')
        self.assertEqual(turn['params']['model'],'provider-terra'); self.assertEqual(turn['params']['effort'],'medium')
