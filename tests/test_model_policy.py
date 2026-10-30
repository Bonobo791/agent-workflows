import unittest
from runner.model_policy import ModelPolicy, ModelPolicyError

class ModelPolicyTests(unittest.TestCase):
    def test_discovers_provider_id_and_applies_medium_to_all_roles(self):
        calls=[]
        def request(method,params):
            calls.append((method,params))
            if 'cursor' not in params: return {'data':[],'nextCursor':'page-2'}
            return {'data':[{'model':'provider-specific-terra-id','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None}
        policy=ModelPolicy(); self.assertEqual(policy.discover(request),'provider-specific-terra-id')
        for role in ('canonical_draft','linkedin_review','implement','qa','post_ready'):
            self.assertEqual(policy.for_role(role),{'model':'provider-specific-terra-id','effort':'medium'})
        self.assertEqual(calls[1][1]['cursor'],'page-2')
        with self.assertRaises(ModelPolicyError): policy.require('qa','other-model')
    def test_unavailable_medium_or_ambiguous_results_fail_closed(self):
        for entries in ([],[{'model':'x','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}],[{'model':'a','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]},{'model':'b','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}]):
            policy=ModelPolicy()
            with self.assertRaises(ModelPolicyError): policy.discover(lambda *_:{'data':entries,'nextCursor':None})
            with self.assertRaises(ModelPolicyError): policy.for_role('implement')
    def test_discovery_failure_clears_previous_selection(self):
        policy=ModelPolicy(); policy.discover(lambda *_:{'data':[{'model':'x','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None})
        with self.assertRaises(ModelPolicyError): policy.discover(lambda *_: {'data':None})
        with self.assertRaises(ModelPolicyError): policy.for_role('qa')
    def test_app_server_discovery_uses_initialized_protocol(self):
        from runner.protocol import AppServer
        class Transport:
            def __init__(self): self.sent=[]; self.responses=[{'id':1,'result':{}},{'id':2,'result':{'data':[{'model':'provider-id','displayName':'GPT-6.1 Terra','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None}}]
            def send(self,item): self.sent.append(item)
            def receive(self): return self.responses.pop(0)
        transport=Transport(); policy=ModelPolicy()
        self.assertEqual(AppServer(transport).discover_model(policy),'provider-id')
        self.assertEqual([x['method'] for x in transport.sent],['initialize','initialized','model/list'])
