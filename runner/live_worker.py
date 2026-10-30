"""Adapter-driven worker. The runtime must enforce isolation before opening a session.

No runtime is configured by default; a fake runtime is used in tests. The
control socket never exposes this object to untrusted clients.
"""
import hashlib
from dataclasses import dataclass
import json
from runner.codex_session import run_codex
from runner.graph_runner import GraphRunner, ExecutionError
from runner.structured import parse_marketing_result
from runner.verification import verify_references, VerificationError

@dataclass(frozen=True)
class VerifiedVerdict:
    passed: bool
    evidence_refs: tuple[str, ...]

class LiveWorker:
    def __init__(self,graph,artifacts,provenance,runtime,gate,skill_resolver=None,required_skills=None):
        self.graph,self.artifacts,self.provenance,self.runtime,self.gate=graph,artifacts,provenance,runtime,gate
        self.skill_resolver=skill_resolver; self.required_skills=required_skills or {}
    def execute(self,run,stage,claim,inputs,prompt,model):
        workflow=self.graph._definition(run).name
        if not isinstance(prompt,str) or not isinstance(model,str) or not model: raise ExecutionError('prompt and model required')
        if not isinstance(inputs,dict) or not isinstance(inputs.get('input_versions'),list): raise ExecutionError('trusted input versions required')
        execution_inputs={'task':inputs,'prompt_hash':hashlib.sha256(prompt.encode('utf-8')).hexdigest(),'model':model}
        if workflow not in ('marketing','engineering'): raise ExecutionError('unknown workflow')
        def specialist(name,context,session):
            skills=context.get('skills',{}) if self.required_skills.get(name) else {}
            hashes={key:hashlib.sha256(value.encode('utf-8')).hexdigest() for key,value in skills.items()}
            # Runtime configuration is trusted operator input, not model output.
            with self.runtime.open_session() as transport:
                request=prompt+'\n\n'+json.dumps({'inputs':inputs,'skills':skills},ensure_ascii=False)
                result=run_codex(transport,self.provenance,run,name,claim,hashes,request,model,session_id=session)
            parsed=parse_marketing_result(result['output'],name)
            if parsed['input_versions'] != inputs['input_versions']: raise ExecutionError('stage input versions do not match')
            if parsed['status']=='passed':
                try: verify_references(self.artifacts,parsed,inputs['input_versions'])
                except VerificationError: raise ExecutionError('stage output unavailable or invalid') from None
            else:
                try:
                    for ref in parsed['outputs']: self.artifacts.get(ref)
                except Exception: raise ExecutionError('stage output unavailable or invalid') from None
            parsed['session_id']=result['session_id']
            bound={'run':run,'stage':name,'inputs':execution_inputs,'result':parsed}
            parsed['_subject_hash']=hashlib.sha256(json.dumps(bound,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            if not self.provenance.verify(run,name,result['session_id'],hashes): raise ExecutionError('session provenance unavailable')
            return parsed
        def gate(name,result):
            if result.get('status') in ('blocked','failed'): return 'blocked'
            if result.get('status')=='awaiting_review': return 'awaiting_review'
            verdict=self.gate(name,result)
            if not isinstance(verdict,VerifiedVerdict) or verdict.passed is not True or not verdict.evidence_refs: return False
            try:
                for ref in verdict.evidence_refs:
                    proof=json.loads(self.artifacts.get(ref))
                    if (not isinstance(proof,dict) or proof.get('run')!=run or proof.get('stage')!=name
                        or proof.get('subject_hash')!=result.get('_subject_hash')
                        or not isinstance(proof.get('reviewer_role'),str) or proof['reviewer_role']==name
                        or not isinstance(proof.get('reviewer_session'),str) or proof['reviewer_session']==result.get('session_id')
                        or not isinstance(proof.get('skills'),dict)): return False
                    if not self.provenance.verify(run,proof['reviewer_role'],proof['reviewer_session'],proof['skills']) or not self.provenance.completed(run,proof['reviewer_role'],proof['reviewer_session']) or not self.provenance.subject_matches(run,proof['reviewer_role'],proof['reviewer_session'],result['_subject_hash']): return False
                    review_ref=proof.get('review_output_ref')
                    if not isinstance(review_ref,str) or review_ref!=self.provenance.completion_hash(run,proof['reviewer_role'],proof['reviewer_session']): return False
                    review_output=self.artifacts.get(review_ref).decode('utf-8')
                    review=parse_marketing_result(review_output,proof['reviewer_role'])
                    if review.get('status')!='passed' or result['_subject_hash'] not in review.get('input_versions',[]) or not review.get('outputs'): return False
                    for review_artifact in review['outputs']: self.artifacts.get(review_artifact)
                    expected=set(self.required_skills.get(proof['reviewer_role'],[]))
                    if set(proof['skills']) != expected: return False
                    if proof['skills']:
                        if not self.skill_resolver: return False
                        resolved=self.skill_resolver.resolve(list(proof['skills']))
                        if {key:value['sha256'] for key,value in resolved.items()}!=proof['skills']: return False
            except Exception: return False
            result['_verified_evidence']=list(verdict.evidence_refs)
            return True
        runner=GraphRunner(self.graph,self.artifacts,specialist,gate,skill_resolver=self.skill_resolver,required_skills=self.required_skills)
        return runner.execute(run,stage,claim,execution_inputs)
