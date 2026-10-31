import json
import tempfile
import unittest
from runner.artifacts import ArtifactStore
from workflows.marketing.openfilm_brief import validate_brief, BriefError


class OpenFilmBriefTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=ArtifactStore(self.tmp.name)
        self.content=self.store.put(b'Approved synthetic statement.')
        self.register=self.store.put(json.dumps({'claims':[{'id':'c1','claim':'synthetic','source_ids':['s1']}],'sources':[{'id':'s1','url':'https://example.invalid','evidence_id':'e1'}]}).encode())
        self.brief={'content_ref':self.content,'assets':[{'channel':'instagram','kind':'png','width':1080,'height':1080,'duration_seconds':None,'approved_text':['Approved synthetic statement.'],'claim_ids':['c1'],'media':[],'captions':'','alt_text':'Synthetic graphic'}]}
    def tearDown(self): self.tmp.cleanup()
    def test_binds_content_claims_and_accessible_brief(self):
        validate_brief(self.store,self.brief,self.content,self.register)
        self.brief['assets'][0]['approved_text']=['Unapproved statement']
        with self.assertRaises(BriefError): validate_brief(self.store,self.brief,self.content,self.register)
    def test_stale_versions_unknown_claims_and_missing_media_block(self):
        with self.assertRaises(BriefError): validate_brief(self.store,self.brief,'f'*64,self.register)
        self.brief['assets'][0]['claim_ids']=['unknown']
        with self.assertRaises(BriefError): validate_brief(self.store,self.brief,self.content,self.register)
        self.brief['assets'][0]['claim_ids']=[]
        self.brief['assets'][0]['media']=[{'artifact_ref':'a'*64,'rights_ref':'b'*64}]
        with self.assertRaises(BriefError): validate_brief(self.store,self.brief,self.content,self.register)
    def test_video_needs_duration_and_captions(self):
        asset=self.brief['assets'][0]; asset.update(kind='mp4',duration_seconds=3,captions='')
        with self.assertRaises(BriefError): validate_brief(self.store,self.brief,self.content,self.register)
        asset['captions']='Synthetic caption'
        validate_brief(self.store,self.brief,self.content,self.register)
    def test_media_and_rights_must_both_be_private_artifacts(self):
        media=self.store.put(b'synthetic image'); rights=self.store.put(b'synthetic license evidence')
        self.brief['assets'][0]['media']=[{'artifact_ref':media,'rights_ref':rights}]
        validate_brief(self.store,self.brief,self.content,self.register)
        self.brief['assets'][0]['media'][0]['rights_ref']='0'*64
        with self.assertRaises(BriefError): validate_brief(self.store,self.brief,self.content,self.register)
    def test_invalid_numeric_fields_do_not_pass(self):
        asset=self.brief['assets'][0]
        asset['width']=True
        with self.assertRaises(BriefError): validate_brief(self.store,self.brief,self.content,self.register)
        asset.update(width=1080,kind='mp4',duration_seconds=float('nan'),captions='Caption')
        with self.assertRaises(BriefError): validate_brief(self.store,self.brief,self.content,self.register)
