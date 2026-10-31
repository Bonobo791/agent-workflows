"""Credential-free workflow integration harness. Not exposed as a network API.

Only trusted local callers and synthetic adapters are supported. Real execution,
remote mutations, authentication and provider access remain disabled.
"""
import json
import hashlib
import re
import os
import stat
import time
from pathlib import Path
from runner.actions import ActionLedger, ActionConflict
from runner.artifacts import ArtifactStore
from runner.graph import GraphStore, GraphError, marketing_definition, engineering_definition
from runner.graph_runner import GraphRunner, ExecutionError
from runner.queue import JobQueue
from runner.provenance import Provenance
from runner.structured import parse_marketing_result
from workflows.engineering.intake import readiness
from workflows.marketing.gates import CHANNELS, intake_issues, canonical_issues, channel_issues

class ServiceError(RuntimeError): pass

class Service:
    def __init__(self,root,skill_resolver=None,required_skills=None,lifecycle=None,lifecycle_policy_verifier=None):
        self.lifecycle=lifecycle
        self.docker_assignments=None
        self.docker_supervisor=None
        self.docker_executor=None
        self.model_supervisor=None
        self.model_executor=None
        self.session_store=None
        self.model_policy=None
        self.stage_dispatcher=None
        self.source_verifier=None
        self.source_tool_authority=None
        self.source_snapshots=None
        self.source_reviewer=None
        self.openfilm_backend=None
        self.openfilm_reviewer=None
        self.audio_transcriber=None
        self.openfilm_platform_authority=None
        self.reddit_context_authority=None
        self.page_rules=None
        self.command_supervisor=None
        self.human_review=None
        self.pr_authority=None
        self.pr_bridge=None
        self.task_intake=None
        self.lifecycle_policy_verifier=lifecycle_policy_verifier
        self.skill_resolver=skill_resolver
        self.required_skills=required_skills or {}
        root=Path(root); root.mkdir(mode=0o700,parents=True,exist_ok=True)
        try:
            directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                info=os.fstat(directory)
                if info.st_uid!=os.geteuid() or info.st_mode & 0o077: raise ServiceError('state directory must be owner-only')
                fd=os.open('state.db',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077: raise ServiceError('unsafe state database')
                finally: os.close(fd)
            finally: os.close(directory)
        except OSError: raise ServiceError('state storage unavailable') from None
        self.graph=GraphStore(str(root/'state.db'))
        self.graph.register(marketing_definition()); self.graph.register(engineering_definition())
        self.provenance=Provenance(self.graph)
        self.actions=ActionLedger(str(root/'state.db'))
        self.queue=JobQueue(str(root/'state.db'))
        artifacts=root/'artifacts'; artifacts.mkdir(mode=0o700,exist_ok=True)
        self.artifacts=ArtifactStore(str(artifacts))
    def close(self):
        if self.session_store is not None: self.session_store.close()
        if self.docker_assignments is not None: self.docker_assignments.close()
        self.queue.close(); self.actions.db.close(); self.graph.close()
    def create(self,run,workflow,task): self.graph.create(run,workflow,task_id=task)
    def enqueue(self,run):
        db=self.graph.db; db.execute('BEGIN IMMEDIATE')
        try:
            self.graph._definition(run)
            row=db.execute('SELECT status FROM graph_runs WHERE id=?',(run,)).fetchone()
            if not row or row[0]!='active': raise ServiceError('run unavailable')
            seq=db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM jobs').fetchone()[0]
            db.execute('INSERT INTO jobs(run,status,seq) VALUES(?,?,?)',(run,'queued',seq))
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK'); raise
    def request_lifecycle_stop(self,run,automatic=False):
        if self.lifecycle is None: raise ServiceError('lifecycle supervisor unavailable')
        if automatic and not self.lifecycle.auto: raise ServiceError('automatic controller unavailable')
        from runner.manual_lifecycle import ManualLifecycle
        if isinstance(self.lifecycle,ManualLifecycle):
            state=self.lifecycle.status(run)
            if state['incarnation']!=self.lifecycle.incarnation or state['status'] not in ('running','pending'): raise ServiceError('manual assignment unavailable')
        row=self.graph.db.execute('SELECT status FROM graph_runs WHERE id=?',(run,)).fetchone()
        if not row: raise ServiceError('run unavailable')
        if row[0]=='active': self._cancel(run)
        elif row[0]!='cancelled': raise ServiceError('run unavailable')
        if automatic:
            self.lifecycle.automatic_stop(run)
            return self.lifecycle.status(run)
        result=self.lifecycle.manual_stop(run)
        if isinstance(self.lifecycle,ManualLifecycle) and self.docker_supervisor is not None:
            rows=self.graph.db.execute("SELECT m.assignment,a.container_id,COALESCE(a.state,'unresolved') FROM docker_execution_map m LEFT JOIN docker_assignments a ON a.run=m.assignment WHERE m.task_run=? AND (a.run IS NULL OR a.state!='terminated') ORDER BY m.assignment",(run,)).fetchall()
            result['docker_assignments']=[{'assignment':row[0],'container_id':row[1],'state':row[2]} for row in rows]
        if isinstance(self.lifecycle,ManualLifecycle) and self.model_supervisor is not None:
            rows=self.graph.db.execute("SELECT unit,state FROM local_scopes WHERE run=? AND state!='terminated' ORDER BY unit",(run,)).fetchall()
            result['local_scopes']=[{'unit':row[0],'state':row[1]} for row in rows]
        return result
    def confirm_lifecycle_stop(self,run,evidence):
        if self.lifecycle is None: raise ServiceError('lifecycle supervisor unavailable')
        lifecycle_db=self.lifecycle.db.execute('PRAGMA database_list').fetchone()[2]
        graph_db=self.graph.db.execute('PRAGMA database_list').fetchone()[2]
        if not lifecycle_db or Path(lifecycle_db).resolve()!=Path(graph_db).resolve(): raise ServiceError('lifecycle and queue must share durable state')
        self.lifecycle.confirm_stopped(run,evidence)
        db=self.graph.db; db.execute('BEGIN IMMEDIATE')
        try:
            row=db.execute('SELECT status,target_hash FROM worker_lifecycle WHERE run=?',(run,)).fetchone()
            if not row or row[0]!='stopped' or db.execute('SELECT 1 FROM lifecycle_operations WHERE target_hash=?',(row[1],)).fetchone(): raise ServiceError('worker termination not fenced')
            job=db.execute('SELECT status FROM jobs WHERE run=?',(run,)).fetchone()
            if job and job[0] in ('cancelling','uncertain'):
                db.execute("UPDATE jobs SET status='cancelled',token=NULL,expires=NULL WHERE run=?",(run,))
            elif job and job[0]!='cancelled': raise ServiceError('job state incompatible with cancellation')
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK'); raise
        return self.lifecycle.status(run)
    def stop_local_scope(self,run,unit):
        from runner.manual_lifecycle import ManualLifecycle
        from runner.local_scope import LocalScopes
        if not isinstance(self.lifecycle,ManualLifecycle) or self.model_supervisor is None: raise ServiceError('local manual supervisor unavailable')
        state=self.lifecycle.status(run)
        graph=self.graph.db.execute('SELECT status FROM graph_runs WHERE id=?',(run,)).fetchone()
        job=self.graph.db.execute('SELECT status FROM jobs WHERE run=?',(run,)).fetchone()
        if state['incarnation']!=self.lifecycle.incarnation or state['status']!='pending' or graph!=('cancelled',) or not job or job[0] not in ('cancelling','uncertain'): raise ServiceError('run must be fenced before stopping its scope')
        scopes=LocalScopes(self.graph.db.execute('PRAGMA database_list').fetchone()[2],self.model_supervisor.backend)
        try:
            if scopes.state(run,unit)=='starting': scopes.bind(run,unit)
            scopes.stop(run,unit)
            return {'unit':unit,'state':'terminated'}
        finally: scopes.close()
    def _cancel(self,run,token=None,require_job=False):
        db=self.graph.db; db.execute('BEGIN IMMEDIATE')
        try:
            if token is not None: self.graph._lease(run,token)
            row=db.execute('SELECT status FROM graph_runs WHERE id=?',(run,)).fetchone()
            if not row or row[0]!='active': raise ServiceError('run unavailable')
            job=db.execute('SELECT status FROM jobs WHERE run=?',(run,)).fetchone()
            if require_job and (not job or job[0] not in ('queued','running')): raise ServiceError('job unavailable')
            if job and job[0] in ('queued','running'):
                db.execute('UPDATE jobs SET status=? WHERE run=?',('cancelled' if job[0]=='queued' else 'cancelling',run))
            db.execute("UPDATE graph_runs SET status='cancelled' WHERE id=?",(run,))
            db.execute('DELETE FROM graph_claims WHERE run=?',(run,))
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK'); raise
    def cancel_job(self,run): self._cancel(run,require_job=True)
    def fence_execution(self,run):
        """Fence an uncertain launch or termination and require manual attestation."""
        from runner.manual_lifecycle import ManualLifecycle
        if not isinstance(self.lifecycle,ManualLifecycle): raise ServiceError('manual lifecycle unavailable')
        return self.request_lifecycle_stop(run)
    def fence_execution_detached(self,run):
        """Fence from a worker thread using connections created in that thread."""
        from runner.manual_lifecycle import ManualLifecycle
        if not isinstance(self.lifecycle,ManualLifecycle): raise ServiceError('manual lifecycle unavailable')
        lifecycle=ManualLifecycle(self.state_db_path,self.lifecycle.target['manual_target'],self.lifecycle.incarnation)
        service=None
        try:
            service=Service(Path(self.state_db_path).parent,lifecycle=lifecycle)
            service.model_supervisor=self.model_supervisor; service.docker_supervisor=self.docker_supervisor
            return service.request_lifecycle_stop(run)
        finally:
            lifecycle.close()
            if service is not None: service.close()
    def cancel(self,run,token): self._cancel(run,token=token)
    def verify_terminal(self,run):
        try:
            row=self.graph.db.execute('SELECT workflow,status FROM graph_runs WHERE id=?',(run,)).fetchone()
            if not row or row[1]!='active': return False
            terminal='assembly' if row[0]=='marketing' else 'post_ready'
            expected='awaiting_review' if row[0]=='marketing' else 'passed'
            state=self.graph.get(run,terminal)
            if not state or state['status']!=expected: return False
            if self.graph.db.execute("SELECT 1 FROM graph_attempts WHERE run=? AND status='uncertain' LIMIT 1",(run,)).fetchone(): return False
            if self.graph.db.execute("SELECT 1 FROM external_actions WHERE run=? AND status IN ('approved','uncertain') LIMIT 1",(run,)).fetchone(): return False
            if self.graph.db.execute('SELECT 1 FROM action_followups WHERE run=? LIMIT 1',(run,)).fetchone(): return False
            if row[0]=='marketing':
                configured=self.graph.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_input_versions'").fetchone()
                admitted=configured and self.graph.db.execute('SELECT 1 FROM task_input_versions WHERE run=?',(run,)).fetchone()
                if admitted:
                    from runner.stage_dispatch import StageDispatcher
                    from runner.source_register import validate
                    dispatcher=self.stage_dispatcher
                    if dispatcher is None:
                        dispatcher=object.__new__(StageDispatcher); dispatcher.service=self
                    brief_state=self.graph.get(run,'brief')
                    if not brief_state or brief_state['status']!='passed': return False
                    brief=json.loads(self.artifacts.get(brief_state['version']))
                    if not isinstance(brief.get('outputs'),list) or len(brief['outputs'])!=2: return False
                    validate(self.artifacts,brief['outputs'][1],brief['_source_verification'])
                    assembly=json.loads(self.artifacts.get(state['version']))
                    for stage in ('brief','canonical_draft','canonical_review','freeze',*[item for channel in CHANNELS for item in (channel+'_draft',channel+'_review')],'asset_brief','asset_production'):
                        current=self.graph.get(run,stage); included=assembly['stages'].get(stage)
                        if not current or not included or any(included.get(key)!=current[key] for key in ('status','version','revision')): return False
                    for review_stage in ('canonical_review',*[channel+'_review' for channel in CHANNELS]): dispatcher._verify_recorded_review(run,review_stage)
                    asset=self.graph.get(run,'asset_production')
                    asset_record=json.loads(self.artifacts.get(asset['version']))
                    if asset['status']=='passed' and 'manifest_ref' not in asset_record: return False
                    if 'manifest_ref' in asset_record:
                        from runner.openfilm_review import verify_evidence
                        if asset_record.get('status')!=asset['status'] or not verify_evidence(self,run,asset_record): return False
            inspector=GraphRunner(self.graph,self.artifacts,lambda *_:None,lambda *_:False,skill_resolver=self.skill_resolver,required_skills=self.required_skills)
            for stage,version in self.graph.db.execute('SELECT stage,version FROM graph_stages WHERE run=?',(run,)):
                record=inspector._validate_artifact_graph(version)
                for ref in record.get('_verified_evidence',[]):
                    proof=json.loads(self.artifacts.get(ref))
                    role=proof.get('reviewer_role'); session=proof.get('reviewer_session'); skills=proof.get('skills')
                    if proof.get('run')!=run or proof.get('stage')!=stage or proof.get('subject_hash')!=record.get('_subject_hash') or not isinstance(skills,dict): return False
                    if set(skills)!=set(self.required_skills.get(role,[])): return False
                    if skills:
                        if not self.skill_resolver: return False
                        resolved=self.skill_resolver.resolve(list(skills))
                        if {name:item['sha256'] for name,item in resolved.items()}!=skills: return False
                    if not self.provenance.verify(run,role,session,skills) or not self.provenance.completed(run,role,session) or not self.provenance.subject_matches(run,role,session,record['_subject_hash']): return False
                    if self.provenance.completion_hash(run,role,session)!=proof.get('review_output_ref'): return False
                    review=parse_marketing_result(self.artifacts.get(proof['review_output_ref']).decode('utf-8'),role)
                    if review['status']!='passed' or record['_subject_hash'] not in review['input_versions']: return False
                names=self.required_skills.get(stage,[])
                if names and not self.skill_resolver: return False
                skills=self.skill_resolver.resolve(names) if names else {}
                fingerprint=hashlib.sha256(json.dumps({name:item['sha256'] for name,item in skills.items()},sort_keys=True).encode()).hexdigest()
                recorded=self.graph.db.execute('SELECT fingerprint FROM graph_skill_state WHERE run=? AND stage=?',(run,stage)).fetchone()
                if (not recorded and names) or (recorded and recorded[0]!=fingerprint): return False
            return True
        except Exception: return False
    def finish_job(self,run,queue_token): self.queue.finish(run,queue_token,verify_completed=self.verify_terminal)
    def claim(self,run): return self.graph.claim(run,60)
    def release(self,run,token): self.graph.release(run,token)
    def approve(self,run,action,version,actor):
        if action=='enable_lifecycle':
            if run!='controller' or self.lifecycle is None or version!=self.lifecycle.policy_hash: raise ServiceError('controller policy mismatch')
        elif action=='docker_config':
            if run!='controller' or self.docker_supervisor is None or self.docker_supervisor.config is None or version!=self.docker_supervisor.config.policy_hash: raise ServiceError('Docker policy mismatch')
        elif action=='command_policy':
            if run!='controller' or self.command_supervisor is None or not self.command_supervisor.policy_hash or version!=self.command_supervisor.policy_hash: raise ServiceError('command policy mismatch')
        elif action=='openfilm_policy':
            if run!='controller' or self.openfilm_backend is None or version!=self.openfilm_backend.policy_hash or self.openfilm_backend._policy()!=version: raise ServiceError('OpenFilm policy mismatch')
        elif action=='openfilm_builder_policy':
            builder=getattr(self.openfilm_backend,'builder',None)
            if run!='controller' or builder is None or version!=getattr(builder,'policy_hash',None) or not callable(getattr(builder,'_policy',None)) or builder._policy()!=version: raise ServiceError('OpenFilm builder policy mismatch')
        elif action=='audio_policy':
            transcriber=self.audio_transcriber
            if run!='controller' or transcriber is None or version!=transcriber.policy_hash or transcriber._current() is not True: raise ServiceError('audio policy mismatch')
        elif action=='docker_kill':
            if self.docker_supervisor is None or self.docker_supervisor.config is None or version!=self.docker_supervisor.escalation_version(run,self.docker_supervisor.config.policy_hash): raise ServiceError('Docker escalation mismatch')
        elif action=='freeze':
            review=self.graph.get(run,'canonical_review')
            if not review or review['status']!='passed' or review['version']!=version: raise ServiceError('review version unavailable or stale')
        else: raise ServiceError('approval action unsupported')
        if action=='enable_lifecycle' and not self.lifecycle.auto:
            prior=self.actions.db.execute('SELECT status FROM external_actions WHERE run=? AND action=? AND version=?',(run,action,version)).fetchone()
            setting=self.lifecycle.db.execute('SELECT enabled FROM lifecycle_settings WHERE policy_hash=?',(version,)).fetchone()
            if prior and prior[0]=='executed' and setting and setting[0]==0:
                self.actions.reauthorize_completed(run,action,version,actor); return
        self.actions.approve(run,action,version,actor)
    def docker_escalate(self,assignment):
        if self.docker_supervisor is None or self.docker_supervisor.config is None: raise ServiceError('Docker supervisor unavailable')
        return self.docker_supervisor.escalate(assignment,self.actions,self.docker_supervisor.config.policy_hash)
    def enable_lifecycle_auto(self):
        if self.lifecycle is None or not callable(self.lifecycle_policy_verifier): raise ServiceError('tested lifecycle policy unavailable')
        version=self.lifecycle.policy_hash; scope='lifecycle-policy:'+self.lifecycle.target_hash
        try: self.actions.reserve_scoped('controller','enable_lifecycle',version,scope)
        except ActionConflict: raise ServiceError('exact policy approval required') from None
        try:
            self.lifecycle.enable_auto(approved_policy=version,verify_policy=self.lifecycle_policy_verifier)
        except Exception:
            self.actions.resolve_scoped('controller','enable_lifecycle',version,scope,'not_executed')
            raise ServiceError('lifecycle policy test unavailable or failed') from None
        try: self.actions.resolve_scoped('controller','enable_lifecycle',version,scope,'executed')
        except Exception:
            self.lifecycle.disable_auto()
            raise ServiceError('controller authorization outcome uncertain') from None
    def restore_lifecycle_auto(self):
        if self.lifecycle is None or not callable(self.lifecycle_policy_verifier): raise ServiceError('tested lifecycle policy unavailable')
        row=self.actions.db.execute("SELECT status FROM external_actions WHERE run='controller' AND action='enable_lifecycle' AND version=?",(self.lifecycle.policy_hash,)).fetchone()
        if not row or row[0]!='executed': raise ServiceError('approved lifecycle policy unavailable')
        try: self.lifecycle.restore_auto(self.lifecycle_policy_verifier)
        except Exception: raise ServiceError('lifecycle policy revalidation failed') from None
    def disable_lifecycle_auto(self):
        if self.lifecycle is None: raise ServiceError('lifecycle supervisor unavailable')
        self.lifecycle.disable_auto()
    def live_execute(self): raise ServiceError('live execution is disabled')
    def complete(self,run,stage,token,evidence):
        if stage in ('open_draft','triage','ready','post_ready'): raise ServiceError('remote actions disabled')
        if not isinstance(evidence,dict): raise ServiceError('invalid evidence')
        workflow=self.graph._definition(run).name
        try:
            if workflow=='marketing' and stage in ('asset_production','assembly'):
                names=self.required_skills.get(stage,[])
                try:
                    if names and not self.skill_resolver: raise ValueError('resolver unavailable')
                    skills=self.skill_resolver.resolve(names) if names else {}
                    hashes={name:skill['sha256'] for name,skill in skills.items()}
                    special_fingerprint=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
                except Exception:
                    if self.graph.get(run,stage):
                        snapshot=self.graph.prepare(run,stage,token)
                        self.graph.invalidate(run,stage,snapshot,token)
                    raise ServiceError('required skills unavailable') from None
                GraphRunner(self.graph,self.artifacts,lambda *_:None,lambda *_:False,skill_resolver=self.skill_resolver,required_skills=self.required_skills)._verify_upstream_skills(run,stage,token)
            if workflow=='marketing' and stage=='asset_production':
                snapshot=self.graph.prepare(run,stage,token)
                ref=self.artifacts.put(b'{"reason":"asset adapter unavailable"}')
                self.graph.finish(run,stage,snapshot,'blocked',ref,token,skill_fingerprint=special_fingerprint)
                return 'blocked'
            if workflow=='marketing' and stage=='assembly':
                snapshot=self.graph.prepare(run,stage,token)
                names=['brief','canonical_draft','canonical_review','freeze'] + [n for channel in CHANNELS for n in (channel+'_draft',channel+'_review')] + ['asset_brief','asset_production']
                summary={}
                for name in names:
                    entry=self.graph.get(run,name)
                    if entry:
                        try: artifact=json.loads(self.artifacts.get(entry['version']))
                        except Exception: raise ServiceError('review artifact unavailable') from None
                        contents=[]
                        try:
                            for output in artifact.get('outputs',[]): contents.append(self.artifacts.get(output).decode('utf-8'))
                        except Exception: raise ServiceError('review content unavailable') from None
                        summary[name]={**entry,'artifact':artifact,'contents':contents}
                        if name=='asset_production' and 'manifest_ref' in artifact:
                            from runner.openfilm_review import verify_evidence
                            if entry['status']!=artifact.get('status') or not verify_evidence(self,run,artifact): raise ServiceError('asset review evidence unavailable')
                            summary[name]['asset_manifest']=json.loads(self.artifacts.get(artifact['manifest_ref']))
                        if name=='brief' and artifact.get('_source_verification'):
                            try: summary[name]['source_verification']=json.loads(self.artifacts.get(artifact['_source_verification']))
                            except Exception: raise ServiceError('source verification unavailable') from None
                ref=self.artifacts.put(json.dumps({'stages':summary},sort_keys=True).encode())
                self.graph.finish(run,stage,snapshot,'awaiting_review',ref,token,skill_fingerprint=special_fingerprint)
                return 'awaiting_review'
            def gate(name,result):
                if workflow=='marketing' and name=='intake': return not intake_issues(result.get('task',{}))
                if workflow=='engineering' and name=='intake': return not readiness(result.get('task',{}),result.get('today',''))
                if workflow=='engineering' and name=='implement':
                    return bool(result.get('output')) and bool(re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})',result.get('commit',''))) and bool(result.get('session_id'))
                if workflow=='engineering' and name in ('test','qa'):
                    prior=self.graph.get(run,'implement')
                    if not prior: return False
                    implementation=json.loads(self.artifacts.get(prior['version']))
                    if not result.get('output') or result.get('commit') != implementation.get('commit'): return False
                    if name=='test': return result.get('passed') is True
                    return result.get('acceptance_passed') is True and bool(result.get('session_id')) and result['session_id'] != implementation.get('session_id')
                if workflow=='marketing' and name=='canonical_draft':
                    return bool(result.get('output')) and bool(result.get('session_id'))
                if workflow=='marketing' and name=='canonical_review':
                    draft=self.graph.get(run,'canonical_draft')
                    if not draft: return False
                    author=json.loads(self.artifacts.get(draft['version'])).get('session_id')
                    if not author or author not in result.get('writer_sessions',[]): return False
                    return not canonical_issues(result.get('reviews',{}),result.get('writer_sessions',[]),result.get('missing_skills',['unknown']),result.get('current_writer_session'))
                if workflow=='marketing' and name.endswith('_review'):
                    channel=name[:-7]
                    if channel in CHANNELS:
                        draft=self.graph.get(run,channel+'_draft')
                        if not draft: return False
                        source=json.loads(self.artifacts.get(draft['version']))
                        reviewer=result.get('session_id')
                        if not reviewer or reviewer==source.get('session_id'): return 'blocked'
                        for other,ref in self.graph.db.execute('SELECT stage,version FROM graph_stages WHERE run=? AND stage<>?',(run,name)):
                            try: evidence=json.loads(self.artifacts.get(ref))
                            except Exception: return 'blocked'
                            def sessions(value):
                                if isinstance(value,dict):
                                    return ([value['session_id']] if isinstance(value.get('session_id'),str) else []) + sum((sessions(v) for v in value.values()),[])
                                if isinstance(value,list): return sum((sessions(v) for v in value),[])
                                return []
                            if reviewer in sessions(evidence): return 'blocked'
                        if not result.get('output'): return 'blocked'
                        if channel=='instagram': return 'blocked'  # independent asset verifier is not wired
                        issues=channel_issues(channel,result)
                        return ('unsuitable' if channel=='reddit' and result.get('relevant') is False else 'blocked') if issues else True
                if workflow=='marketing' and name.endswith('_draft') and name[:-6] in CHANNELS:
                    return bool(result.get('output')) and bool(result.get('session_id'))
                if workflow=='marketing' and name=='freeze': return bool(result.get('review_ref')) and bool(result.get('content_ref'))
                return isinstance(result.get('output'),str) and bool(result['output'])
            if workflow=='marketing' and stage=='freeze':
                names=self.required_skills.get(stage,[])
                try:
                    if names and not self.skill_resolver: raise ValueError('resolver unavailable')
                    if names: self.skill_resolver.resolve(names)
                except Exception: raise ServiceError('required skills unavailable') from None
                GraphRunner(self.graph,self.artifacts,lambda *_:None,lambda *_:False,skill_resolver=self.skill_resolver,required_skills=self.required_skills)._verify_upstream_skills(run,stage,token)
                if evidence: raise ServiceError('freeze accepts no replacement content')
                prior=self.graph.get(run,'canonical_review')
                if not prior: raise ServiceError('canonical review missing')
                version=prior['version']
                draft=self.graph.get(run,'canonical_draft')
                if not draft: raise ServiceError('canonical draft missing')
                frozen={'review_ref':version,'content_ref':draft['version']}
                db=self.graph.db
                db.execute('BEGIN IMMEDIATE')
                try:
                    snapshot=self.graph.prepare(run,stage,token)
                    if snapshot['dependencies'].get('canonical_review') != prior['revision']: raise ServiceError('review changed')
                    if db.execute("SELECT 1 FROM external_actions WHERE run=? AND action='freeze' AND status='uncertain'",(run,)).fetchone(): raise ServiceError('prior approval outcome unresolved')
                    cur=db.execute("UPDATE external_actions SET status='uncertain',updated=? WHERE run=? AND action='freeze' AND version=? AND status='approved'",(time.time(),run,version))
                    if not cur.rowcount: raise ServiceError('exact approval required')
                    db.execute('COMMIT')
                except Exception:
                    db.execute('ROLLBACK'); raise
                try:
                    result=GraphRunner(self.graph,self.artifacts,lambda name,i,_session: i['data'] if self.required_skills.get(name) else i,gate,skill_resolver=self.skill_resolver,required_skills=self.required_skills).execute(run,stage,token,frozen,required_dependencies={'canonical_review':prior['revision']})
                    self.actions.resolve(run,'freeze',version,'executed')
                    return result
                except Exception: raise ServiceError('freeze outcome requires inspection') from None
            ref=GraphRunner(self.graph,self.artifacts,lambda name,i,_session: i['data'] if self.required_skills.get(name) else i,gate,skill_resolver=self.skill_resolver,required_skills=self.required_skills).execute(run,stage,token,evidence)
            outcome=self.graph.get(run,stage)['status']
            return ref if outcome=='passed' else outcome
        except (GraphError,ActionConflict,ExecutionError) as exc: raise ServiceError(str(exc)) from None
