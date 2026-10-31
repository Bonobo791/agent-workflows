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
    def test_snapshot_handoff_requires_valid_source_evidence(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from runner.stage_dispatch import _source_snapshot_inputs
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                evidence=service.artifacts.put(json.dumps({'source_bundle_ref':'bundle'}).encode())
                brief={'outputs':['brief','register'],'_source_review':evidence}
                service.source_snapshots=SimpleNamespace(contents=lambda ref:[{'content_ref':'source','content':'primary text'}])
                with patch('runner.source_reviewer.validate_evidence') as verify:
                    self.assertEqual(_source_snapshot_inputs(service,'run',brief),('bundle',[{'content_ref':'source','content':'primary text'}]))
                    verify.assert_called_once_with(service,'run','register',evidence)
                with patch('runner.source_reviewer.validate_evidence',side_effect=SourceError('stale')):
                    with self.assertRaises(SourceError): _source_snapshot_inputs(service,'run',brief)
            finally:service.close()
    def test_snapshot_review_binds_actual_turn_inputs_and_rejects_stale_receipt(self):
        from runner.source_snapshots import SourceSnapshots
        from tests.test_source_snapshots import Fetcher
        from runner.source_reviewer import validate_evidence
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                skill=Path(root)/'skills'/'role';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('fixture')
                service.skill_resolver=SkillResolver([skill.parent]);service.required_skills={'source_review':['role']}
                service.create('r','marketing','task');claim=service.claim('r')
                register=service.artifacts.put(json.dumps(REGISTER).encode());draft=service.artifacts.put(b'current draft')
                fetcher=Fetcher();service.source_snapshots=SourceSnapshots(service,fetcher)
                service.graph.db.execute('CREATE TABLE completed_turn_outputs(run TEXT,role TEXT,session TEXT,turn TEXT,input_versions TEXT,output_refs TEXT)')
                class Executor:
                    counter=0
                    def review(self,**kw):
                        self.counter+=1;session="session-"+str(self.counter)
                        assert 'retrieved primary evidence' in kw['prompt']
                        assert 'supported and primary are booleans' in kw['prompt']
                        assert 'Each finding evidence must exactly match one SHA-256 string in input_versions' in kw['prompt']
                        assert 'When draft is null, there is no draft to assess' in kw['prompt']
                        assert 'Return only passed or blocked' in kw['prompt']
                        judgment=verifier(REGISTER,register);judgment.update(draft_ref=draft,omitted_claims=[],coverage_complete=True)
                        result_ref=service.artifacts.put(json.dumps(judgment).encode())
                        response=json.dumps({'stage':'source_review','status':'passed','input_versions':kw['input_versions'],'outputs':[result_ref],'findings':[]}).encode();response_ref=service.artifacts.put(response)
                        service.provenance.bind('r','source_review',session,kw['skills'],claim,fresh=True,subject=register)
                        service.provenance.mark_completed('r','source_review',session,claim,hashlib.sha256(response).hexdigest())
                        service.graph.db.execute('INSERT INTO completed_turn_outputs VALUES(?,?,?,?,?,?)',('r','source_review',session,'turn',json.dumps(kw['input_versions']),json.dumps([result_ref])))
                        proof={'run':'r','reviewer_role':'source_review','reviewer_session':session,'subject_hash':register,'skills':kw['skills'],'review_output_ref':response_ref}
                        events={'run':'r','role':'source_review','session':session,'turn':'turn','items':[]}
                        return {'outputs':[result_ref],'session_id':session,'turn_id':'turn','response_ref':response_ref,'evidence_ref':service.artifacts.put(json.dumps(proof).encode()),'tool_evidence_ref':service.artifacts.put(json.dumps(events).encode())}
                service.docker_executor=Executor();reviewer=SourceReviewer(service)
                evidence=reviewer.verify('r',claim,register,draft_ref=draft,model='fixture',cancelled=lambda:False)
                self.assertTrue(validate_evidence(service,'r',register,evidence,draft))
                fetcher.urls={*fetcher.urls,'https://other.invalid/additional'}
                service.source_snapshots=SourceSnapshots(service,fetcher)
                with self.assertRaises(SourceError): validate_evidence(service,'r',register,evidence,draft)
                refreshed=reviewer.verify('r',claim,register,draft_ref=draft,model='fixture',cancelled=lambda:False)
                self.assertNotEqual(refreshed,evidence)
                self.assertEqual(service.docker_executor.counter,2)
                self.assertTrue(validate_evidence(service,'r',register,refreshed,draft))
                with self.assertRaises(SourceError): validate_evidence(service,'r',register,evidence,draft)
                from runner.stage_dispatch import _source_snapshot_inputs
                # Brief source evidence has no draft; draft-bound evidence cannot substitute for it.
                with self.assertRaises(SourceError): _source_snapshot_inputs(service,'r',{'outputs':['brief',register],'_source_review':refreshed})
                service.graph.db.execute("UPDATE completed_turn_outputs SET input_versions='[]'")
                with self.assertRaises(SourceError): validate_evidence(service,'r',register,refreshed,draft)
            finally:service.close()
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
