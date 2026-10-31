"""Synthetic integration checks against the shared service, no live adapters."""
import tempfile
import unittest
from runner.service import Service, ServiceError

class MarketingIntegrationReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.s = Service(self.temp.name)
        self.s.create('r','marketing','task'); self.token = self.s.claim('r')
        task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
        self.s.complete('r','intake',self.token,{'task':task})
        for stage in ('brief','canonical_draft'): self.s.complete('r',stage,self.token,{'output':stage,'session_id':'writer'})
        reviews={r:{'status':'passed','session_id':r,'findings':[]} for r in ('editorial','factual','seo_geo')}
        self.review={'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]}
    def tearDown(self): self.s.close(); self.temp.cleanup()
    def freeze(self):
        self.s.complete('r','canonical_review',self.token,self.review)
        self.s.approve('r','freeze',self.s.graph.get('r','canonical_review')['version'],'reviewer')
        self.s.complete('r','freeze',self.token,{})
    def test_review_failures_and_missing_skills_stop_freeze(self):
        self.review['reviews']['factual']['status']='failed'
        with self.assertRaises(ServiceError): self.s.complete('r','canonical_review',self.token,self.review)
        self.review['reviews']['factual']['status']='passed'; self.review['missing_skills']=['Humanizer']
        with self.assertRaises(ServiceError): self.s.complete('r','canonical_review',self.token,self.review)
        with self.assertRaises(ServiceError): self.s.complete('r','freeze',self.token,{})
    def test_channel_change_invalidates_only_its_review(self):
        self.freeze()
        for channel in ('linkedin','x'):
            self.s.complete('r',channel+'_draft',self.token,{'output':channel,'session_id':'writer-'+channel})
            self.s.complete('r',channel+'_review',self.token,{'output':'review','session_id':'reviewer'})
        old=self.s.graph.get('r','x_review')
        self.s.complete('r','linkedin_draft',self.token,{'output':'edited','session_id':'writer-linkedin'})
        self.assertIsNone(self.s.graph.get('r','linkedin_review'))
        self.assertEqual(old,self.s.graph.get('r','x_review'))
    def test_source_change_invalidates_freeze_and_channels(self):
        self.freeze(); self.s.complete('r','linkedin_draft',self.token,{'output':'draft','session_id':'writer'})
        self.s.complete('r','brief',self.token,{'output':'changed source'})
        self.assertIsNone(self.s.graph.get('r','freeze'))
        self.assertIsNone(self.s.graph.get('r','linkedin_draft'))
    def test_release_and_resume_preserves_outputs_cancel_prevents_writes(self):
        self.freeze(); self.s.release('r',self.token); self.token=self.s.claim('r')
        self.s.complete('r','x_draft',self.token,{'output':'draft','session_id':'writer'})
        self.s.graph.cancel('r',self.token)
        with self.assertRaises(ServiceError): self.s.complete('r','x_review',self.token,{'output':'review','session_id':'reviewer'})
    def test_no_publish_and_live_execution(self):
        self.freeze()
        with self.assertRaises(ServiceError): self.s.complete('r','publish',self.token,{'output':'post'})
        with self.assertRaises(ServiceError): self.s.live_execute()
    def test_blocked_channel_cannot_be_assembled_via_service(self):
        self.freeze()
        self.s.complete('r','asset_brief',self.token,{'output':'brief'})
        self.assertEqual(self.s.complete('r','asset_production',self.token,{}),'blocked')
        self.s.complete('r','instagram_draft',self.token,{'output':'draft','session_id':'writer'})
        self.assertEqual(self.s.complete('r','instagram_review',self.token,{'output':'review','session_id':'reviewer'}),'blocked')
        self.assertEqual(self.s.graph.get('r','instagram_review')['status'],'blocked')
        self.s.complete('r','reddit_draft',self.token,{'output':'draft','session_id':'writer'})
        self.assertEqual(self.s.complete('r','reddit_review',self.token,{'output':'review','session_id':'reddit-reviewer','community':'example','rules_ref':'r','rules_current':True,'relevant':False,'product_mentioned':False}),'unsuitable')
        self.assertEqual(self.s.graph.get('r','reddit_review')['status'],'unsuitable')
        with self.assertRaises(ServiceError): self.s.complete('r','assembly',self.token,{})

if __name__=='__main__': unittest.main()
