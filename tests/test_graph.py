import sqlite3
import tempfile
import unittest
from runner.graph import GraphStore, GraphError, marketing_definition, engineering_definition, _legacy_marketing_definition
from runner.queue import JobQueue

class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=GraphStore(self.tmp.name+'/db'); self.db.register(marketing_definition()); self.db.register(engineering_definition()); self.db.create('m','marketing'); self.db.create('e','engineering'); self.tokens={'m':self.db.claim('m',60),'e':self.db.claim('e',60)}
    def tearDown(self): self.db.close(); self.tmp.cleanup()
    def put(self, run, stage, status='passed', version='v1'):
        snapshot=self.db.prepare(run,stage,self.tokens[run])
        self.db.finish(run,stage,snapshot,status,version,self.tokens[run])
    def test_standalone_graph_can_complete_its_attempt(self):
        self.db.begin_attempt('e','intake','input',self.tokens['e'],2)
        self.db.finish('e','intake',self.db.prepare('e','intake',self.tokens['e']),'passed','output',self.tokens['e'],completion_input='input')
        self.assertEqual(self.db.db.execute("SELECT output_hash FROM graph_results WHERE run='e' AND stage='intake' AND input_hash='input'").fetchone(),('output',))
    def test_standalone_pr_reconciliation_fails_closed_when_integrations_absent(self):
        for stage in ('intake','plan','implement','test','qa'): self.put('e',stage)
        snapshot=self.db.prepare('e','open_draft',self.tokens['e'])
        binding={'proposal':'p','action':'a','head':'h','scope':'s','result_ref':'r','kind':'draft'}
        with self.assertRaises(GraphError): self.db.finish('e','open_draft',snapshot,'passed','v',self.tokens['e'],pr_reconcile=binding)
        self.assertIsNone(self.db.get('e','open_draft'))
    def test_pr_reconciliation_uses_graph_clock_for_action_timestamp(self):
        from runner.actions import ActionLedger
        ledger=ActionLedger(self.tmp.name+'/db')
        try:
            self.db.clock=lambda:1234
            self.db.db.execute('UPDATE graph_claims SET expires=2000 WHERE run=?',('e',))
            for stage in ('intake','plan','implement','test','qa'): self.put('e',stage)
            self.db.db.execute('CREATE TABLE pr_bridge_operations(proposal TEXT PRIMARY KEY,run TEXT,kind TEXT,state TEXT,result TEXT)')
            self.db.db.execute("INSERT INTO pr_bridge_operations VALUES('p','e','draft','uncertain',NULL)")
            ledger.db.execute("INSERT INTO external_actions VALUES('e','a','h','actor','uncertain',0)")
            ledger.db.execute("INSERT INTO action_scopes VALUES('s','e','a','h')")
            binding={'proposal':'p','action':'a','head':'h','scope':'s','result_ref':'r','kind':'draft'}
            self.db.finish('e','open_draft',self.db.prepare('e','open_draft',self.tokens['e']),'passed','v',self.tokens['e'],pr_reconcile=binding)
            self.assertEqual(ledger.db.execute("SELECT updated FROM external_actions WHERE run='e'").fetchone(),(1234,))
        finally: ledger.db.close()
    def test_draft_reconciles_without_followups_but_ready_requires_them(self):
        for stage in ('intake','plan','implement','test','qa'): self.put('e',stage)
        db=self.db.db
        db.execute('CREATE TABLE external_actions(run TEXT,action TEXT,version TEXT,status TEXT,updated REAL)')
        db.execute('CREATE TABLE action_scopes(scope TEXT,run TEXT,action TEXT,version TEXT)')
        db.execute('CREATE TABLE pr_bridge_operations(proposal TEXT,run TEXT,kind TEXT,state TEXT,result TEXT)')
        db.execute("INSERT INTO external_actions VALUES('e','draft-action','head','uncertain',0)")
        db.execute("INSERT INTO action_scopes VALUES('draft-scope','e','draft-action','head')")
        db.execute("INSERT INTO pr_bridge_operations VALUES('draft-proposal','e','draft','uncertain',NULL)")
        draft={'proposal':'draft-proposal','action':'draft-action','head':'head','scope':'draft-scope','result_ref':'receipt','kind':'draft'}
        self.db.finish('e','open_draft',self.db.prepare('e','open_draft',self.tokens['e']),'passed','v',self.tokens['e'],pr_reconcile=draft)
        self.assertEqual(self.db.get('e','open_draft')['status'],'passed')
        self.put('e','triage')
        db.execute("INSERT INTO external_actions VALUES('e','ready-action','head','uncertain',0)")
        db.execute("INSERT INTO action_scopes VALUES('ready-scope','e','ready-action','head')")
        db.execute("INSERT INTO pr_bridge_operations VALUES('ready-proposal','e','ready','uncertain',NULL)")
        ready={'proposal':'ready-proposal','action':'ready-action','head':'head','scope':'ready-scope','result_ref':'receipt','kind':'ready'}
        with self.assertRaisesRegex(GraphError,'PR reconciliation unavailable'): self.db.finish('e','ready',self.db.prepare('e','ready',self.tokens['e']),'passed','v',self.tokens['e'],pr_reconcile=ready)
        self.assertIsNone(self.db.get('e','ready'))
        self.assertEqual(db.execute("SELECT status FROM external_actions WHERE action='ready-action'").fetchone(),('uncertain',))
    def test_stage_audit_is_durable_and_failure_rolls_back_transition(self):
        self.put('m','intake')
        self.put('e','intake')
        reopened=GraphStore(self.tmp.name+'/db')
        try:
            rows=reopened.db.execute('SELECT schema_version,run,stage,status,version FROM graph_audit ORDER BY id').fetchall()
            self.assertEqual(rows,[(1,'m','intake','passed','v1'),(1,'e','intake','passed','v1')])
        finally: reopened.close()
        self.db.db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON graph_audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.put('m','intake',version='v2')
        self.assertEqual(self.db.get('m','intake')['version'],'v1')
    def test_stage_history_records_invalidated_derivatives(self):
        self.put('e','intake'); self.put('e','plan'); self.put('e','intake',version='v2')
        history=self.db.db.execute('SELECT stage,status,version FROM graph_audit WHERE run=? ORDER BY id',('e',)).fetchall()
        self.assertEqual(history,[('intake','passed','v1'),('plan','passed','v1'),('plan','invalidated','v1'),('intake','passed','v2')])
    def test_rate_limit_resume_is_recorded(self):
        self.put('e','intake','rate_limited')
        self.db.resume_rate_limited('e','intake',self.tokens['e'])
        rows=self.db.db.execute('SELECT status FROM graph_audit WHERE run=? ORDER BY id',('e',)).fetchall()
        self.assertEqual(rows,[('rate_limited',),('resumed',)])
    def test_claim_audit_failure_keeps_claim_active(self):
        rows=self.db.db.execute('SELECT task_id,status FROM claim_audit WHERE run=?',('e',)).fetchall()
        self.assertEqual(rows,[('e','claimed')])
        self.db.db.execute("CREATE TRIGGER reject_claim_audit BEFORE INSERT ON claim_audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.db.release('e',self.tokens['e'])
        self.db.prepare('e','intake',self.tokens['e'])
    def test_rejected_duplicate_claim_is_audited_without_replacing_owner(self):
        self.db.create('duplicate','marketing','e')
        with self.assertRaisesRegex(GraphError,'already claimed'): self.db.claim('duplicate',60)
        self.assertEqual(self.db.db.execute("SELECT run,task_id,status FROM claim_audit WHERE run='duplicate'").fetchall(),[('duplicate','e','rejected')])
        self.db.prepare('e','intake',self.tokens['e'])
        self.db.db.execute("CREATE TRIGGER reject_duplicate_audit BEFORE INSERT ON claim_audit WHEN NEW.status='rejected' BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.db.claim('duplicate',60)
        self.db.prepare('e','intake',self.tokens['e'])
    def test_attempt_history_tracks_retries_and_rolls_back_on_audit_failure(self):
        self.db.begin_attempt('e','intake','v1',self.tokens['e'],2)
        self.db.resolve_attempt('e','intake','v1',self.tokens['e'],'failed')
        self.db.begin_attempt('e','intake','v1',self.tokens['e'],2)
        rows=self.db.db.execute('SELECT count,status FROM attempt_audit ORDER BY id').fetchall()
        self.assertEqual(rows,[(1,'uncertain'),(1,'failed'),(2,'uncertain')])
        self.db.db.execute("CREATE TRIGGER reject_attempt_audit BEFORE INSERT ON attempt_audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.db.resolve_attempt('e','intake','v1',self.tokens['e'],'completed')
        self.assertEqual(self.db.db.execute('SELECT status FROM graph_attempts').fetchone(),('uncertain',))
    def test_parallel_fanout_assembly_and_scoped_invalidation(self):
        for stage in ('intake','brief','canonical_draft','canonical_review','freeze'): self.put('m',stage)
        for channel in ('linkedin','x','facebook','reddit'):
            self.put('m',channel+'_draft'); self.put('m',channel+'_review')
        self.put('m','instagram_draft')
        with self.assertRaises(GraphError): self.db.prepare('m','instagram_review',self.tokens['m'])
        self.put('m','asset_brief'); self.put('m','asset_production','blocked')
        self.put('m','instagram_review','blocked')
        self.put('m','assembly','awaiting_review')
        self.db.db.execute("CREATE TRIGGER reject_auto_invalidation BEFORE INSERT ON invalidation_audit BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.put('m','linkedin_draft',version='v2')
        self.assertEqual(self.db.get('m','linkedin_draft')['version'],'v1')
        self.assertEqual(self.db.get('m','linkedin_review')['status'],'passed')
        self.assertEqual(self.db.get('m','assembly')['status'],'awaiting_review')
        self.db.db.execute('DROP TRIGGER reject_auto_invalidation')
        self.put('m','linkedin_draft',version='v2')
        self.assertEqual(self.db.db.execute("SELECT stage,reason FROM invalidation_audit ORDER BY stage").fetchall(),[('assembly','upstream_changed'),('linkedin_review','upstream_changed')])
        self.assertIsNone(self.db.get('m','linkedin_review'))
        self.assertIsNone(self.db.get('m','assembly'))
        self.assertEqual(self.db.get('m','x_review')['status'],'passed')
        self.put('m','asset_production','passed','asset-v2')
        self.assertIsNone(self.db.get('m','instagram_review'))
        self.assertEqual(self.db.get('m','x_review')['status'],'passed')
    def test_legacy_marketing_migration_preserves_unaffected_work(self):
        graph=self.db; token=self.tokens['m']
        graph.definitions['marketing']=_legacy_marketing_definition()
        graph.db.execute('UPDATE graph_runs SET definition_hash=? WHERE id=?',(_legacy_marketing_definition().fingerprint(),'m'))
        for stage in ('intake','brief','canonical_draft','canonical_review','freeze','instagram_draft','instagram_review','linkedin_draft','linkedin_review','x_draft','x_review','facebook_draft','facebook_review','reddit_draft','reddit_review','asset_brief'):
            self.put('m',stage)
        self.put('m','asset_production','blocked'); self.put('m','assembly','awaiting_review')
        graph.definitions['marketing']=marketing_definition()
        queue=JobQueue(self.tmp.name+'/db')
        try:
            queue.enqueue('m'); claim=queue.claim('worker',60)['token']
            self.assertTrue(graph.migrate_marketing_assets('m',token,claim))
            self.assertEqual(graph.db.execute("SELECT stage,reason FROM invalidation_audit ORDER BY stage").fetchall(),[('assembly','graph_migration'),('instagram_review','graph_migration')])
            self.assertIsNone(graph.get('m','instagram_review'))
            self.assertIsNone(graph.get('m','assembly'))
            self.assertEqual(graph.get('m','linkedin_review')['status'],'passed')
            snapshot=graph.prepare('m','instagram_review',token)
            self.assertEqual(snapshot['dependencies']['asset_production'],graph.get('m','asset_production')['revision'])
            with self.assertRaises(GraphError): graph.finish('m','instagram_review',snapshot,'passed','v1',token)
            self.assertFalse(graph.migrate_marketing_assets('m',token,claim))
            self.assertEqual(graph.db.execute('SELECT COUNT(*) FROM graph_migrations').fetchone(),(1,))
        finally: queue.close()
    def test_legacy_marketing_migration_rejects_uncertain_attempt(self):
        graph=self.db; token=self.tokens['m']; graph.definitions['marketing']=_legacy_marketing_definition()
        graph.db.execute('UPDATE graph_runs SET definition_hash=? WHERE id=?',(_legacy_marketing_definition().fingerprint(),'m'))
        graph.begin_attempt('m','intake','input',token,2)
        graph.definitions['marketing']=marketing_definition(); queue=JobQueue(self.tmp.name+'/db')
        try:
            queue.enqueue('m'); claim=queue.claim('worker',60)['token']
            with self.assertRaises(GraphError): graph.migrate_marketing_assets('m',token,claim)
            self.assertEqual(graph.db.execute('SELECT definition_hash FROM graph_runs WHERE id=?',('m',)).fetchone(),(_legacy_marketing_definition().fingerprint(),))
        finally: queue.close()
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
