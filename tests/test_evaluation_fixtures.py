"""Public, credential-free evaluation cases for workflow evidence gates."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from runner.artifacts import ArtifactStore
from runner.source_register import SourceError, validate
from tests.source_fixture import REGISTER, verifier
from tests.test_assets import png
from workflows.engineering.triage import TriageError, review_snapshot
from workflows.marketing.assets import verify_asset
from workflows.marketing.gates import canonical_issues, channel_issues
from workflows.marketing.openfilm_brief import BriefError, validate_brief


class EvaluationFixtures(unittest.TestCase):
    def test_marketing_source_review_channel_and_actual_asset_cases(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root); content=store.put(b'Approved synthetic statement.')
            register=store.put(json.dumps(REGISTER).encode()); proof=store.put(json.dumps(verifier(REGISTER,register)).encode())
            self.assertTrue(validate(store,register,proof))
            invalid=verifier(REGISTER,register); invalid['checks'][0]['supported']=False
            with self.assertRaises(SourceError): validate(store,register,store.put(json.dumps(invalid).encode()))
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            self.assertEqual(canonical_issues(reviews,['writer'],[],'writer'),[])
            reviews['factual']['session_id']='writer'
            self.assertTrue(canonical_issues(reviews,['writer'],[],'writer'))
            self.assertTrue(channel_issues('reddit',{'community':'example','rules_ref':'fixture','rules_current':False,'relevant':True,'product_mentioned':False}))
            image=png(); (Path(root)/'fixture.png').write_bytes(image); digest=hashlib.sha256(image).hexdigest()
            spec={'channel':'instagram','kind':'png','width':2,'height':3,'duration_seconds':None,'approved_text':['Approved synthetic statement.'],'claim_ids':['claim-1'],'media':[],'captions':'','alt_text':'Synthetic red image'}
            brief={'content_ref':content,'assets':[spec]}
            self.assertTrue(validate_brief(store,brief,content,register))
            with self.assertRaises(BriefError): validate_brief(store,brief,'0'*64,register)
            run={'brief_version':'v1','project_version':'p1','asset_sha256':digest}
            self.assertEqual(verify_asset(root,'fixture.png',{**spec,'version':'v1'},run,'p1')['status'],'awaiting_review')
            self.assertEqual(verify_asset(root,'fixture.png',{**spec,'version':'v1'},run,'p2')['status'],'blocked')
            self.assertTrue(channel_issues('instagram',{'asset':{'status':'awaiting_review','ref':digest}}))

    def test_engineering_ci_and_review_disposition_cases(self):
        head='a'*40
        snapshot={'head':head,'checks':[{'sha':head,'status':'success'}],'comments':[{'id':'review:1'}],'unresolved_change_requests':[]}
        self.assertTrue(review_snapshot(snapshot,head,{'review:1':'invalid'}))
        self.assertFalse(review_snapshot(snapshot,head,{'review:1':'valid'}))
        wrong=copy.deepcopy(snapshot); wrong['checks'][0]['sha']='b'*40
        with self.assertRaises(TriageError): review_snapshot(wrong,head,{'review:1':'invalid'})
        missing=copy.deepcopy(snapshot); missing['checks']=[]
        with self.assertRaises(TriageError): review_snapshot(missing,head,{'review:1':'invalid'})
        with self.assertRaises(TriageError): review_snapshot(snapshot,head,{})
