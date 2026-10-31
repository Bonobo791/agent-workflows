import tempfile
import unittest
from runner.graph import GraphStore, marketing_definition
from runner.artifacts import ArtifactStore
from runner.graph_runner import GraphRunner, ExecutionError

class GraphRunnerTests(unittest.TestCase):
    def test_cache_reuse_audit_covers_both_workflows(self):
        from runner.graph import engineering_definition
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.register(engineering_definition())
            try:
                runner=GraphRunner(graph,ArtifactStore(root),lambda stage,inputs,session:{'body':'synthetic'},lambda stage,result:True)
                for workflow in ('engineering','marketing'):
                    graph.create(workflow,workflow); token=graph.claim(workflow,60)
                    runner.execute(workflow,'intake',token,{})
                    runner.execute(workflow,'intake',token,{})
                self.assertEqual(graph.db.execute('SELECT run,status FROM cache_audit ORDER BY run').fetchall(),[('engineering','reused'),('marketing','reused')])
            finally: graph.close()
    def test_marketing_writer_revision_resumes_but_review_is_fresh(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            calls=[]
            def specialist(stage,inputs,session):
                calls.append((stage,session))
                return {'body':'synthetic','session_id':session or stage+'-session'}
            runner=GraphRunner(graph,ArtifactStore(root),specialist,lambda stage,result:True)
            for stage in ('intake','brief','canonical_draft','canonical_review'):
                runner.execute('run',stage,token,{'revision':1})
            runner.execute('run','canonical_draft',token,{'revision':2})
            runner.execute('run','canonical_review',token,{'revision':2})
            self.assertEqual(calls[-2:], [('canonical_draft','canonical_draft-session'),('canonical_review',None)])
            graph.close()

    def test_mocked_stage_records_verified_artifact_and_blocks_gate_failure(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            store=ArtifactStore(root)
            calls=[]
            def specialist(stage,inputs,session):
                calls.append(stage)
                return {'body':'synthetic'}
            runner=GraphRunner(graph,store,specialist, lambda stage,result: stage=='intake')
            ref=runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertEqual(graph.get('run','intake')['version'],ref)
            self.assertEqual(runner.execute('run','intake',token,{'task':'synthetic'}),ref)
            self.assertEqual(calls,['intake'])
            self.assertEqual(graph.db.execute("SELECT stage,status,output_hash FROM cache_audit").fetchall(),[('intake','reused',ref)])
            graph.db.execute("CREATE TRIGGER reject_cache_audit BEFORE INSERT ON cache_audit BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END")
            with self.assertRaises(Exception): runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertEqual(calls,['intake'])
            self.assertEqual(graph.get('run','intake')['version'],ref)
            with self.assertRaises(ExecutionError): runner.execute('run','brief',token,{'source':ref})
            self.assertIsNone(graph.get('run','brief'))
            graph.close()

    def test_changed_input_invalidates_old_output_even_if_new_attempt_fails(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            store=ArtifactStore(root)
            runner=GraphRunner(graph,store,lambda stage,inputs,session: inputs,lambda stage,result: result.get('valid') is True)
            runner.execute('run','intake',token,{'valid':True})
            runner.execute('run','brief',token,{'valid':True})
            with self.assertRaises(ExecutionError): runner.execute('run','intake',token,{'valid':False})
            self.assertIsNone(graph.get('run','intake'))
            self.assertIsNone(graph.get('run','brief'))
            self.assertEqual(graph.db.execute('SELECT stage,reason FROM invalidation_audit ORDER BY stage').fetchall(),[('brief','upstream_invalidated'),('intake','cache_miss')])
            graph.close()

    def test_cache_hit_rechecks_current_gate(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            store=ArtifactStore(root); permitted=[True]
            runner=GraphRunner(graph,store,lambda stage,inputs,session:{'body':'synthetic'},lambda stage,result:permitted[0])
            runner.execute('run','intake',token,{'task':'synthetic'})
            permitted[0]=False
            with self.assertRaises(ExecutionError): runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertIsNone(graph.get('run','intake'))
            self.assertEqual(graph.db.execute('SELECT reason FROM invalidation_audit').fetchone(),('gate_changed',))
            graph.close()

    def test_invalidation_audit_failure_rolls_back_stage_deletion(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            try:
                runner=GraphRunner(graph,ArtifactStore(root),lambda stage,inputs,session:{'body':'synthetic'},lambda stage,result:True)
                ref=runner.execute('run','intake',token,{})
                graph.db.execute("CREATE TRIGGER reject_invalidation_audit BEFORE INSERT ON invalidation_audit BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
                with self.assertRaises(Exception): graph.invalidate('run','intake',graph.prepare('run','intake',token),token,reason='cache_miss')
                self.assertEqual(graph.get('run','intake')['version'],ref)
                self.assertEqual(graph.db.execute("SELECT COUNT(*) FROM graph_audit WHERE status='invalidated'").fetchone()[0],0)
            finally: graph.close()

    def test_result_commit_failure_keeps_stage_and_attempt_consistent(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            runner=GraphRunner(graph,ArtifactStore(root),lambda stage,inputs,session:{'body':'synthetic'},lambda stage,result:True)
            graph.db.execute("CREATE TRIGGER fail_result BEFORE INSERT ON graph_results BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
            with self.assertRaises(ExecutionError): runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertIsNone(graph.get('run','intake'))
            with self.assertRaises(Exception): runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertIsNone(graph.get('run','intake'))
            graph.close()

    def test_skill_changes_invalidate_cached_output(self):
        from pathlib import Path
        from runner.skills import SkillResolver
        with tempfile.TemporaryDirectory() as root:
            skillroot=Path(root)/'skills'; skillroot.mkdir(); skill=skillroot/'editor'; skill.mkdir(); (skill/'SKILL.md').write_text('first')
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            calls=[]
            def specialist(stage,inputs,session): calls.append(inputs['skills']['editor']); return {'body':'synthetic'}
            runner=GraphRunner(graph,ArtifactStore(root),specialist,lambda stage,result:True,skill_resolver=SkillResolver([skillroot]),required_skills={'intake':['editor']})
            runner.execute('run','intake',token,{'task':'synthetic'})
            (skill/'SKILL.md').write_text('second')
            runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertEqual(calls,['first','second'])
            graph.close()

    def test_missing_required_skill_invalidates_prior_pass(self):
        from pathlib import Path
        from runner.skills import SkillResolver
        with tempfile.TemporaryDirectory() as root:
            skillroot=Path(root)/'skills'; skillroot.mkdir(); skill=skillroot/'editor'; skill.mkdir(); file=skill/'SKILL.md'; file.write_text('first')
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            runner=GraphRunner(graph,ArtifactStore(root),lambda stage,inputs,session:{'body':'synthetic'},lambda stage,result:True,skill_resolver=SkillResolver([skillroot]),required_skills={'intake':['editor']})
            runner.execute('run','intake',token,{'task':'synthetic'}); file.unlink()
            with self.assertRaises(ExecutionError): runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertIsNone(graph.get('run','intake'))
            graph.close()

    def test_changed_upstream_skill_blocks_successor_without_rerunning_upstream(self):
        from pathlib import Path
        from runner.skills import SkillResolver
        with tempfile.TemporaryDirectory() as root:
            skillroot=Path(root)/'skills'; skillroot.mkdir(); skill=skillroot/'editor'; skill.mkdir(); file=skill/'SKILL.md'; file.write_text('first')
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            runner=GraphRunner(graph,ArtifactStore(root),lambda stage,inputs,session:{'body':'synthetic'},lambda stage,result:True,skill_resolver=SkillResolver([skillroot]),required_skills={'intake':['editor']})
            runner.execute('run','intake',token,{'task':'synthetic'}); file.write_text('changed')
            with self.assertRaises(ExecutionError): runner.execute('run','brief',token,{'task':'synthetic'})
            self.assertIsNone(graph.get('run','intake'))
            graph.close()

    def test_implementation_resumes_saved_session(self):
        from runner.graph import engineering_definition
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(engineering_definition()); graph.create('run','engineering'); token=graph.claim('run',60)
            calls=[]
            def specialist(stage,inputs,session):
                if stage=='implement': calls.append(session); return {'session_id':session or 'implementation-session','body':inputs['body']}
                return {'body':'initial'}
            runner=GraphRunner(graph,ArtifactStore(root),specialist,lambda stage,result:True)
            runner.execute('run','intake',token,{}); runner.execute('run','plan',token,{})
            runner.execute('run','implement',token,{'body':'first'})
            runner.execute('run','implement',token,{'body':'fix'})
            self.assertEqual(calls,[None,'implementation-session'])
            graph.close()

    def test_terminal_blocked_stage_is_reused(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            calls=[]
            runner=GraphRunner(graph,ArtifactStore(root),lambda stage,inputs,session:(calls.append(stage) or {'body':'synthetic'}),lambda stage,result:'blocked')
            for _ in range(3): runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertEqual(calls,['intake'])
            self.assertEqual(graph.get('run','intake')['status'],'blocked')
            graph.close()

    def test_subscription_limit_is_persisted_as_pause(self):
        from runner.protocol import RateLimited
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60)
            def specialist(*args): raise RateLimited('limit')
            runner=GraphRunner(graph,ArtifactStore(root),specialist,lambda stage,result:True)
            with self.assertRaises(ExecutionError): runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertEqual(graph.get('run','intake')['status'],'rate_limited'); graph.close()

    def test_rate_limit_stays_paused_until_explicit_resume(self):
        from runner.protocol import RateLimited
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('run','marketing'); token=graph.claim('run',60); calls=[]
            def specialist(*args): calls.append(1); raise RateLimited('limit')
            runner=GraphRunner(graph,ArtifactStore(root),specialist,lambda stage,result:True,max_attempts=1)
            with self.assertRaises(ExecutionError): runner.execute('run','intake',token,{'task':'synthetic'})
            with self.assertRaises(ExecutionError): runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertEqual(len(calls),1)
            graph.resume_quota('default'); graph.resume_rate_limited('run','intake',token)
            with self.assertRaises(ExecutionError): runner.execute('run','intake',token,{'task':'synthetic'})
            self.assertEqual(len(calls),2); graph.close()

    def test_quota_pause_applies_to_other_runs(self):
        from runner.protocol import RateLimited
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('a','marketing'); graph.create('b','marketing'); a=graph.claim('a',60); b=graph.claim('b',60); calls=[]
            def specialist(*args): calls.append(1); raise RateLimited('limit')
            runner=GraphRunner(graph,ArtifactStore(root),specialist,lambda stage,result:True)
            with self.assertRaises(ExecutionError): runner.execute('a','intake',a,{})
            with self.assertRaises(ExecutionError): runner.execute('b','intake',b,{})
            self.assertEqual(len(calls),1)
            graph.resume_quota('default')
            with self.assertRaises(ExecutionError): runner.execute('b','intake',b,{})
            self.assertEqual(len(calls),2); graph.close()

    def test_awaiting_review_is_reused_without_specialist_rerun(self):
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60); calls=[]
            runner=GraphRunner(graph,ArtifactStore(root),lambda *args:(calls.append(1) or {'body':'draft'}),lambda stage,result:'awaiting_review')
            runner.execute('r','intake',token,{}); runner.execute('r','intake',token,{})
            self.assertEqual(len(calls),1); self.assertEqual(graph.get('r','intake')['status'],'awaiting_review'); graph.close()

    def test_missing_upstream_output_invalidates_before_successor(self):
        import json, os
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60); store=ArtifactStore(root)
            output=store.put(b'output')
            runner=GraphRunner(graph,store,lambda stage,inputs,session:{'outputs':[output]},lambda stage,result:True)
            runner.execute('r','intake',token,{})
            os.chmod(root+'/'+output,0o600); os.unlink(root+'/'+output)
            with self.assertRaises(ExecutionError): runner.execute('r','brief',token,{})
            self.assertIsNone(graph.get('r','intake')); graph.close()

    def test_missing_output_invalidates_cached_stage(self):
        import os
        with tempfile.TemporaryDirectory() as root:
            graph=GraphStore(root+'/db'); graph.register(marketing_definition()); graph.create('r','marketing'); token=graph.claim('r',60); store=ArtifactStore(root)
            output=store.put(b'output'); runner=GraphRunner(graph,store,lambda stage,inputs,session:{'outputs':[output]},lambda stage,result:True)
            runner.execute('r','intake',token,{})
            os.chmod(root+'/'+output,0o600); os.unlink(root+'/'+output)
            with self.assertRaises(ExecutionError): runner.execute('r','intake',token,{})
            self.assertIsNone(graph.get('r','intake')); graph.close()
