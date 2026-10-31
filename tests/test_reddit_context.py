import hashlib
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from runner.graph_runner import GraphRunner
from runner.service import Service
from runner.stage_dispatch import StageDispatcher, StageError


class RedditContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.service=Service(self.tmp.name)
        service=self.service; service.create('run','marketing','task'); service.enqueue('run')
        self.queue=service.queue.claim('worker',60)['token']; self.claim=service.claim('run')
        store=service.artifacts; self.subject=store.put(b'Synthetic Reddit draft')
        empty=store.put(json.dumps({'outputs':[]}).encode()); draft=store.put(json.dumps({'outputs':[self.subject],'session_id':'writer'}).encode())
        for stage in ('intake','brief','canonical_draft','canonical_review','freeze','reddit_draft'):
            service.graph.finish('run',stage,service.graph.prepare('run',stage,self.claim),'passed',draft if stage=='reddit_draft' else empty,self.claim)
        service.graph.db.execute('CREATE TABLE task_input_versions(run TEXT PRIMARY KEY,source_version TEXT NOT NULL,artifact_ref TEXT NOT NULL)')
        service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('run','v1',empty))
        class Skills:
            def resolve(self,names): return {name:{'content':'synthetic','sha256':hashlib.sha256(b'synthetic').hexdigest()} for name in names}
        class Model:
            def for_role(self,role): return {'model':'fixture','effort':'medium'}
        service.skill_resolver=Skills(); service.model_policy=Model(); service.required_skills={'reddit_review':['reviewer']}
        class Executor:
            def review(self,subject,**kwargs):
                session='reviewer'; output_ref=store.put(b'synthetic review')
                output=json.dumps({'stage':'reddit_review','status':'passed','input_versions':kwargs['input_versions'],'outputs':[output_ref],'findings':[]})
                response=store.put(output.encode()); service.provenance.bind('run','reddit_review',session,kwargs['skills'],kwargs['claim'],fresh=True,subject=subject)
                service.provenance.mark_completed('run','reddit_review',session,kwargs['claim'],response)
                evidence={'run':'run','stage':'reddit_draft','subject_hash':subject,'reviewer_role':'reddit_review','reviewer_session':session,'skills':kwargs['skills'],'review_output_ref':response}
                return {'session_id':session,'outputs':[output_ref],'output':output,'evidence_ref':store.put(json.dumps(evidence).encode())}
        service.docker_executor=Executor()
        class Authority:
            relevant=True
            product=False
            disclosure=False
            def check(self,run,draft_ref):
                now=time.time(); rules=store.put(b'Synthetic community rules')
                return store.put(json.dumps({'draft_ref':draft_ref,'community':'example','rules_ref':rules,'checked_at':now,'expires_at':now+300,'relevant':self.relevant,'product_mentioned':self.product,'affiliation_disclosed':self.disclosure}).encode())
            def verify(self,run,draft_ref,context_ref): return True
        service.reddit_context_authority=Authority(); self.dispatcher=StageDispatcher(service)
        self.patcher=patch.object(GraphRunner,'_verify_upstream_skills',return_value=None); self.patcher.start()
    def tearDown(self): self.patcher.stop(); self.service.close(); self.tmp.cleanup()
    def test_current_authenticated_context_allows_review_and_is_rechecked(self):
        result=self.dispatcher._model_stage('run','reddit_review',self.queue,self.claim)
        self.assertEqual(result['status'],'passed'); self.assertTrue(self.dispatcher._verify_recorded_review('run','reddit_review'))
        self.service.reddit_context_authority.verify=lambda *_:False
        with self.assertRaises(StageError): self.dispatcher._verify_recorded_review('run','reddit_review')
    def test_irrelevant_context_is_unsuitable(self):
        self.service.reddit_context_authority.relevant=False
        result=self.dispatcher._model_stage('run','reddit_review',self.queue,self.claim)
        self.assertEqual(result['status'],'unsuitable'); self.assertTrue(self.dispatcher._verify_recorded_review('run','reddit_review'))
    def test_missing_context_stays_blocked(self):
        self.service.reddit_context_authority=None
        result=self.dispatcher._model_stage('run','reddit_review',self.queue,self.claim)
        self.assertEqual(result['status'],'blocked'); self.assertTrue(self.dispatcher._verify_recorded_review('run','reddit_review'))
    def test_product_without_disclosure_stays_blocked(self):
        self.service.reddit_context_authority.product=True
        result=self.dispatcher._model_stage('run','reddit_review',self.queue,self.claim)
        self.assertEqual(result['status'],'blocked'); self.assertTrue(self.dispatcher._verify_recorded_review('run','reddit_review'))
        self.service.reddit_context_authority.verify=lambda *_:False
        self.assertTrue(self.dispatcher._verify_recorded_review('run','reddit_review'))
    def test_context_expiry_invalidates_recorded_review(self):
        result=self.dispatcher._model_stage('run','reddit_review',self.queue,self.claim)
        self.assertEqual(result['status'],'passed')
        with patch('runner.reddit_context.time.time',return_value=time.time()+400):
            with self.assertRaises(StageError): self.dispatcher._verify_recorded_review('run','reddit_review')
