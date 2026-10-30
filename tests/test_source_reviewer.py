import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from runner.service import Service
from runner.skills import SkillResolver
from runner.source_register import SourceError
from runner.source_reviewer import SourceReviewer, SourceReviewBlocked
from tests.source_fixture import REGISTER, verifier

class SourceReviewerTests(unittest.TestCase):
    def test_rejects_missing_unsupported_stale_and_omitted_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                skill=Path(root)/'skills'/'role'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('fixture')
                service.skill_resolver=SkillResolver([skill.parent]); service.required_skills={'source_review':['role']}
                service.create('r','marketing','task'); claim=service.claim('r')
                register=service.artifacts.put(json.dumps(REGISTER).encode()); draft=service.artifacts.put(b'current draft')
                class Executor:
                    mode='ok'; counter=0
                    def review(self,**kw):
                        judgment=verifier(REGISTER,register); judgment.update(draft_ref=draft,omitted_claims=[],coverage_complete=True)
                        if self.mode=='unsupported': judgment['checks'][0]['supported']=False
                        if self.mode=='missing': judgment['checks']=[]
                        if self.mode=='stale': judgment['draft_ref']='old'
                        if self.mode=='omitted': judgment['omitted_claims']=['unregistered material claim']
                        data=json.dumps(judgment).encode(); ref=service.artifacts.put(data)
                        items=[] if self.mode=='unavailable' else [{'type':'mcpToolCall','server':'source-evidence','tool':'fetch_primary','status':'completed'}]
                        self.counter+=1; session='session-'+str(self.counter)
                        event={'run':'r','role':'source_review','session':session,'turn':'t','items':items}
                        response=json.dumps({'stage':'source_review','status':'passed','input_versions':kw['input_versions'],'outputs':[ref],'findings':[]}).encode(); response_ref=service.artifacts.put(response)
                        service.provenance.bind('r','source_review',session,kw['skills'],claim,fresh=True,subject=register)
                        service.provenance.mark_completed('r','source_review',session,claim,hashlib.sha256(response).hexdigest())
                        proof={'run':'r','reviewer_role':'source_review','reviewer_session':session,'subject_hash':register,'skills':kw['skills'],'review_output_ref':response_ref}
                        return {'outputs':[ref],'session_id':session,'turn_id':'t','response_ref':response_ref,'evidence_ref':service.artifacts.put(json.dumps(proof).encode()),'tool_evidence_ref':service.artifacts.put(json.dumps(event).encode())}
                service.docker_executor=Executor(); service.source_tool_authority=lambda events,reg,current: bool(events['items']) and reg==register and current==draft
                reviewer=SourceReviewer(service)
                self.assertTrue(reviewer.verify('r','claim',register,draft_ref=draft,model='fixture',cancelled=lambda:False))
                for mode in ('unsupported','missing','stale','omitted','unavailable'):
                    service.docker_executor.mode=mode
                    with self.subTest(mode=mode), self.assertRaises(SourceError) as failure: reviewer.verify('r','claim',register,draft_ref=draft,model='fixture',cancelled=lambda:False)
                    if mode in ('unsupported','missing','omitted'):
                        self.assertIsInstance(failure.exception,SourceReviewBlocked)
                        blocked=json.loads(service.artifacts.get(failure.exception.evidence_ref))
                        self.assertEqual(blocked['draft_ref'],draft)
                        if mode=='omitted': self.assertEqual(blocked['reviewer_judgment']['omitted_claims'],['unregistered material claim'])
                service.docker_executor.mode='ok'; service.source_tool_authority=None
                with self.assertRaises(SourceError): reviewer.verify('r','claim',register,draft_ref=draft,model='fixture',cancelled=lambda:False)
            finally: service.close()
