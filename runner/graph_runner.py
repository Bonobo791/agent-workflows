"""Mockable stage runner. External actions and live execution are not enabled."""
import hashlib
import json
from runner.graph import GraphError
from runner.protocol import RateLimited
from workflows.marketing.gates import CHANNELS

class ExecutionError(RuntimeError):
    pass

# A specialist cannot invoke a remote mutation by choosing a stage name.
EXTERNAL_STAGES={'open_draft','ready','triage','post_ready'}

class GraphRunner:
    def __init__(self, graph, artifacts, specialist, gate, max_attempts=2, skill_resolver=None, required_skills=None, quota_scope='default'):
        self.graph, self.artifacts, self.specialist, self.gate = graph, artifacts, specialist, gate
        self.max_attempts=max_attempts
        self.quota_scope=quota_scope
        self.skill_resolver=skill_resolver
        self.required_skills=required_skills or {}
    def _validate_artifact_graph(self,ref):
        value=json.loads(self.artifacts.get(ref))
        if not isinstance(value,dict): raise ExecutionError('invalid stage artifact')
        for key in ('outputs','_verified_evidence'):
            refs=value.get(key,[])
            if not isinstance(refs,list): raise ExecutionError('invalid artifact references')
            for item in refs:
                data=self.artifacts.get(item)
                if key=='_verified_evidence':
                    proof=json.loads(data)
                    if not isinstance(proof,dict) or not isinstance(proof.get('review_output_ref'),str): raise ExecutionError('invalid verification artifact')
                    review=json.loads(self.artifacts.get(proof['review_output_ref']))
                    if not isinstance(review,dict) or not isinstance(review.get('outputs'),list) or not review['outputs']: raise ExecutionError('invalid reviewer output')
                    for review_ref in review['outputs']: self.artifacts.get(review_ref)
        return value
    def _verify_upstream_skills(self,run,stage,token):
        definition=self.graph._definition(run)
        if stage not in definition.dependencies: raise ExecutionError('unknown stage')
        visited=set()
        def visit(node):
            for parent in definition.dependencies[node]:
                if parent in visited: continue
                visited.add(parent); visit(parent)
                if not self.graph.get(run,parent): continue
                names=self.required_skills.get(parent,[])
                try:
                    self._validate_artifact_graph(self.graph.get(run,parent)['version'])
                    if names and not self.skill_resolver: raise ValueError('missing resolver')
                    skills=self.skill_resolver.resolve(names) if names else {}
                    hashes={name:value['sha256'] for name,value in skills.items()}
                    fingerprint=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
                    row=self.graph.db.execute('SELECT fingerprint FROM graph_skill_state WHERE run=? AND stage=?',(run,parent)).fetchone()
                    changed=(not row and bool(names)) or (row and row[0]!=fingerprint)
                except Exception: changed=True
                if changed:
                    snapshot=self.graph.prepare(run,parent,token)
                    self.graph.invalidate(run,parent,snapshot,token,reason='upstream_unavailable')
                    raise ExecutionError('upstream skill inputs changed')
        visit(stage)
    def execute(self,run,stage,token,inputs,required_dependencies=None,correction_id=None):
        if self.graph.quota_paused(self.quota_scope): raise ExecutionError('subscription quota paused; explicit resume required')
        if stage in EXTERNAL_STAGES: raise ExecutionError('external stage requires a separate approved integration')
        self._verify_upstream_skills(run,stage,token)
        snapshot=self.graph.prepare(run,stage,token)
        if required_dependencies is not None and snapshot['dependencies'] != required_dependencies:
            raise ExecutionError('approved dependency changed')
        names=self.required_skills.get(stage,[])
        try:
            if names and not self.skill_resolver: raise ExecutionError('required skill resolver unavailable')
            skills=self.skill_resolver.resolve(names) if names else {}
        except Exception:
            if self.graph.get(run,stage): self.graph.invalidate(run,stage,snapshot,token,reason='skills_unavailable')
            raise ExecutionError('required skills unavailable') from None
        hashes={name:skill['sha256'] for name,skill in skills.items()}
        skill_fingerprint=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
        try:
            encoded=json.dumps({'inputs':inputs,'dependencies':snapshot['dependencies'],'skill_hashes':hashes},sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        except (ValueError, TypeError): raise ExecutionError('inputs are not serializable') from None
        input_hash=hashlib.sha256(encoded).hexdigest()
        row=self.graph.db.execute('SELECT output_hash FROM graph_results WHERE run=? AND stage=? AND input_hash=?',(run,stage,input_hash)).fetchone()
        current=self.graph.get(run,stage)
        if current and current['status']=='rate_limited': raise ExecutionError('subscription limit reached; explicit resume required')
        if row and current and current['status'] in ('passed','blocked','unsuitable','awaiting_review') and current['version']==row[0]:
            try: cached=self._validate_artifact_graph(row[0])
            except Exception:
                self.graph.invalidate(run,stage,snapshot,token,reason='artifact_unavailable')
                raise ExecutionError('cached artifact unavailable') from None
            decision=self.gate(stage,cached)
            cached_status='passed' if decision is True else decision
            if cached_status != current['status']:
                self.graph.invalidate(run,stage,snapshot,token,reason='gate_changed')
                raise ExecutionError('cached stage no longer passes its gate')
            self.graph.record_cache_reuse(run,stage,snapshot,token,input_hash,row[0],self.quota_scope)
            return row[0]
        if current and correction_id is None:
            self.graph.invalidate(run,stage,snapshot,token,reason='cache_miss')
            snapshot=self.graph.prepare(run,stage,token)
        self.graph.begin_attempt(run,stage,input_hash,token,self.max_attempts,correction_id=correction_id)
        try:
            context={'data':inputs,'skills':{name:skill['content'] for name,skill in skills.items()}} if names else inputs
            workflow=self.graph._definition(run).name
            resumable=(workflow=='engineering' and stage=='implement') or (workflow=='marketing' and stage in {'canonical_draft', *(channel+'_draft' for channel in CHANNELS)})
            row=self.graph.db.execute('SELECT session FROM graph_sessions WHERE run=? AND role=?',(run,stage)).fetchone() if resumable else None
            session=row[0] if row else None
            try: result=self.specialist(stage,context,session)
            except RateLimited:
                if correction_id is not None:
                    self.graph.db.execute('INSERT OR REPLACE INTO quota_pauses VALUES(?,?,?)',(self.quota_scope,run,stage))
                    raise ExecutionError('subscription limit reached; correction outcome requires inspection') from None
                ref=self.artifacts.put(b'{"status":"rate_limited"}')
                self.graph.finish(run,stage,snapshot,'rate_limited',ref,token,completion_input=input_hash,skill_fingerprint=skill_fingerprint,quota_scope=self.quota_scope)
                raise ExecutionError('subscription limit reached; run paused') from None
            if resumable and session and isinstance(result,dict) and result.get('session_id') != session: raise ExecutionError('writer or implementation session changed')
            decision=self.gate(stage,result) if isinstance(result,dict) else False
            if decision is True: status='passed'
            elif decision in ('blocked','unsuitable','awaiting_review'):
                status=decision
                result={**result, '_gate_status':status}
            else:
                self.graph.resolve_attempt(run,stage,input_hash,token,'failed')
                raise ExecutionError('stage gate failed')
            output=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            ref=self.artifacts.put(output)
            self.graph.finish(run,stage,snapshot,status,ref,token,completion_input=input_hash,skill_fingerprint=skill_fingerprint,session_record=result.get('session_id') if resumable else None,correction_id=correction_id)
            return ref
        except ExecutionError: raise
        except Exception: raise ExecutionError('stage outcome requires inspection') from None
