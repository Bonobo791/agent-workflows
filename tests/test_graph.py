import tempfile
import unittest
from runner.graph import GraphStore, GraphError, marketing_definition, engineering_definition

class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=GraphStore(self.tmp.name+'/db'); self.db.register(marketing_definition()); self.db.register(engineering_definition()); self.db.create('m','marketing'); self.db.create('e','engineering'); self.tokens={'m':self.db.claim('m',60),'e':self.db.claim('e',60)}
    def tearDown(self): self.db.close(); self.tmp.cleanup()
    def put(self, run, stage, status='passed', version='v1'):
        snapshot=self.db.prepare(run,stage,self.tokens[run])
        self.db.finish(run,stage,snapshot,status,version,self.tokens[run])
    def test_parallel_fanout_assembly_and_scoped_invalidation(self):
        for stage in ('intake','brief','canonical_draft','canonical_review','freeze'): self.put('m',stage)
        for channel in ('linkedin','x','facebook','instagram','reddit'):
            self.put('m',channel+'_draft'); self.put('m',channel+'_review')
        self.put('m','asset_brief'); self.put('m','asset_production','blocked')
        self.put('m','assembly','awaiting_review')
        self.put('m','linkedin_draft',version='v2')
        self.assertIsNone(self.db.get('m','linkedin_review'))
        self.assertIsNone(self.db.get('m','assembly'))
        self.assertEqual(self.db.get('m','x_review')['status'],'passed')
    def test_stale_result_cannot_finish_after_input_changed(self):
        self.put('m','intake'); snapshot=self.db.prepare('m','brief',self.tokens['m']); self.put('m','intake',version='v2')
        with self.assertRaises(GraphError): self.db.finish('m','brief',snapshot,'passed','v1',self.tokens['m'])
    def test_engineering_requires_previous_stage(self):
        with self.assertRaises(GraphError): self.db.prepare('e','implement',self.tokens['e'])
        self.put('e','intake'); self.put('e','plan'); self.put('e','implement')

    def test_blocked_draft_can_propagate_but_cannot_become_passed_review(self):
        for stage in ('intake','brief','canonical_draft','canonical_review','freeze'): self.put('m',stage)
        self.put('m','reddit_draft','blocked')
        snapshot=self.db.prepare('m','reddit_review',self.tokens['m'])
        with self.assertRaises(GraphError): self.db.finish('m','reddit_review',snapshot,'passed','v1',self.tokens['m'])
        self.db.finish('m','reddit_review',snapshot,'blocked','v1',self.tokens['m'])

    def test_exclusive_task_claim_and_expired_worker_cannot_finish(self):
        clock=[0]
        other=GraphStore(self.tmp.name+'/other', clock=lambda: clock[0])
        try:
            other.register(engineering_definition()); other.create('r1','engineering',task_id='same'); other.create('r2','engineering',task_id='same')
            token=other.claim('r1', ttl=5)
            snapshot=other.prepare('r1','intake',token)
            with self.assertRaises(GraphError): other.claim('r2', ttl=5)
            clock[0]=6; replacement=other.claim('r2', ttl=5)
            with self.assertRaises(GraphError): other.finish('r1','intake',snapshot,'passed','v1',token)
            other.finish('r2','intake',other.prepare('r2','intake',replacement),'passed','v1',replacement)
        finally: other.close()

    def test_cancel_prevents_late_result(self):
        snapshot=self.db.prepare('m','intake',self.tokens['m'])
        self.db.cancel('m',self.tokens['m'])
        with self.assertRaises(GraphError): self.db.finish('m','intake',snapshot,'passed','v1',self.tokens['m'])
        with self.assertRaises(GraphError): self.db.claim('m',60)

    def test_rate_limited_or_awaiting_approval_do_not_unblock_successors(self):
        self.put('e','intake','rate_limited')
        with self.assertRaises(GraphError): self.db.prepare('e','plan',self.tokens['e'])
        self.put('e','intake','awaiting_approval')
        with self.assertRaises(GraphError): self.db.prepare('e','plan',self.tokens['e'])

    def test_attempts_survive_restart_and_are_bounded_by_input_version(self):
        token=self.tokens['m']
        self.assertEqual(self.db.begin_attempt('m','intake','input-v1',token,2),1)
        with self.assertRaises(GraphError): self.db.begin_attempt('m','intake','input-v1',token,2)
        self.db.resolve_attempt('m','intake','input-v1',token,'failed')
        reopened=GraphStore(self.tmp.name+'/db'); reopened.register(marketing_definition())
        try: self.assertEqual(reopened.begin_attempt('m','intake','input-v1',token,2),2)
        finally: reopened.close()
        self.db.resolve_attempt('m','intake','input-v1',token,'failed')
        with self.assertRaises(GraphError): self.db.begin_attempt('m','intake','input-v1',token,2)
        self.assertEqual(self.db.begin_attempt('m','intake','input-v2',token,2),1)

    def test_terminal_same_artifact_with_changed_skill_updates_revision(self):
        snapshot=self.db.prepare('m','intake',self.tokens['m'])
        self.db.finish('m','intake',snapshot,'blocked','same',self.tokens['m'],skill_fingerprint='old')
        before=self.db.get('m','intake')['revision']
        snapshot=self.db.prepare('m','intake',self.tokens['m'])
        self.db.finish('m','intake',snapshot,'blocked','same',self.tokens['m'],skill_fingerprint='new')
        self.assertNotEqual(before,self.db.get('m','intake')['revision'])
        self.assertEqual(self.db.db.execute("SELECT fingerprint FROM graph_skill_state WHERE run='m' AND stage='intake'").fetchone()[0],'new')

    def test_session_is_persisted_before_turn_and_cannot_be_replaced(self):
        self.db.bind_session('e','implement','session-one',self.tokens['e'])
        self.assertEqual(self.db.session('e','implement'),'session-one')
        with self.assertRaises(GraphError): self.db.bind_session('e','implement','session-two',self.tokens['e'])
