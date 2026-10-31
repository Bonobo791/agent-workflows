"""Independent, subject-bound review of produced private media."""
import hashlib
import json
import time
from runner.codex_executor import _bounded
from runner.graph import GraphError
from runner.graph_runner import GraphRunner
from runner.openfilm_stage import OpenFilmStageError, _verify_project
from runner.structured import parse_marketing_result


def _media_references(service,assets,brief_ref,project_ref):
    if not isinstance(assets,list) or not assets: raise ValueError('assets unavailable')
    refs=[]; previews=[]; reports=[]
    for item in assets:
        if item['artifact_ref']!=item['sha256'] or item['brief_version']!=brief_ref or item['project_version']!=project_ref: raise ValueError('asset binding changed')
        service.artifacts.get(item['artifact_ref']); refs.append(item['artifact_ref'])
        if not isinstance(item['previews'],list) or not item['previews']: raise ValueError('preview unavailable')
        for preview in item['previews']:
            if preview['artifact_ref']!=preview['sha256']: raise ValueError('preview binding changed')
            service.artifacts.get(preview['artifact_ref']); previews.append(preview['artifact_ref'])
        report_ref=item['look_report_ref']; report=json.loads(service.artifacts.get(report_ref))
        if not isinstance(report,dict) or set(report)!={'ok','lines'} or report['ok'] is not True or not isinstance(report['lines'],list) or any(not isinstance(line,str) or line.startswith('! text cut off ') for line in report['lines']) or not any(line.startswith('✓ ') and 'same picture' in line for line in report['lines']): raise ValueError('visual check unavailable')
        reports.append(report_ref)
    return refs,previews,reports


def _validate_report(report, refs, parsed, inputs):
    checks={'artifact_ref','content_fidelity','brand_fidelity','rights','platform_suitability','readability','captions','safe_zones','audio'}
    if parsed['input_versions']!=inputs or parsed['status'] not in ('passed','blocked') or len(parsed['outputs'])!=1 or any(item['evidence'] not in inputs for item in parsed['findings']): raise ValueError('review binding invalid')
    if not isinstance(report,dict) or set(report)!={'assets'} or not isinstance(report['assets'],list) or len(report['assets'])!=len(refs): raise ValueError('asset report unavailable')
    for item,ref in zip(report['assets'],refs):
        if not isinstance(item,dict) or set(item)!=checks or item['artifact_ref']!=ref or any(type(item[k]) is not bool for k in checks-{'artifact_ref'}): raise ValueError('asset checks unavailable')
    if parsed['status']=='passed' and any(not all(item[k] for k in checks-{'artifact_ref'}) for item in report['assets']): raise ValueError('asset checks failed')
    if parsed['status']=='blocked' and not any(item['severity']=='blocking' and item['resolution']=='open' for item in parsed['findings']): raise ValueError('blocking finding unavailable')

def _review_inputs(service,reviewer,run,session,subject,refs,access,inputs,timeout,cancelled):
    if _bounded(lambda:reviewer.verify_access(run,session,subject,refs,access),time.monotonic()+timeout,cancelled) is not True: raise ValueError('media access unverified')
    extras=getattr(reviewer,'extra_inputs',None)
    if not callable(extras): return inputs,[]
    values=_bounded(lambda:extras(run,session,subject,refs,access),time.monotonic()+timeout,cancelled)
    if not isinstance(values,list) or len(values)>100 or any(not isinstance(ref,str) for ref in values): raise ValueError('sample references unavailable')
    for ref in values: service.artifacts.get(ref)
    return inputs+values,values

def _coverage(reviewer,run,session,subject,refs,access,extra):
    method=getattr(reviewer,'coverage',None)
    if callable(method): return method(run,session,subject,refs,access)
    return 'Video review used 12 sampled frames; human playback is required.' if extra else None


def verify_evidence(service,run,record):
    """Recheck private media, fresh review provenance and independent access."""
    try:
        reviewer=service.openfilm_reviewer; backend=service.openfilm_backend
        timeout=reviewer.timeout_seconds
        if type(timeout) is not int or not 1<=timeout<=600: return False
        names=service.required_skills['asset_review']; resolved=service.skill_resolver.resolve(names)
        skills={name:item['sha256'] for name,item in resolved.items()}
        session=record['review_session']; manifest_ref=record['manifest_ref']
        if not names or skills!=record['review_skills'] or not service.provenance.verify(run,'asset_review',session,skills) or not service.provenance.completed(run,'asset_review',session) or not service.provenance.subject_matches(run,'asset_review',session,manifest_ref) or service.provenance.completion_hash(run,'asset_review',session)!=record['response_ref']: return False
        manifest=json.loads(service.artifacts.get(manifest_ref)); supervisor=json.loads(service.artifacts.get(manifest['supervisor_ref']))
        brief_stage=json.loads(service.artifacts.get(service.graph.get(run,'asset_brief')['version']))
        if manifest['status']!='awaiting_review' or brief_stage['outputs'][0]!=manifest['brief_ref'] or supervisor['attempt_id']!=manifest['attempt_id'] or supervisor['project_ref']!=manifest['project_ref'] or supervisor['terminal_ref']!=manifest['terminal_ref']: return False
        from runner.openfilm_platform import verify_platform
        verify_platform(service,run,manifest['brief_ref'],json.loads(service.artifacts.get(manifest['brief_ref'])),manifest['platform_ref'],timeout,lambda:False)
        if _bounded(lambda:backend.verify_terminal(run,manifest['attempt_id'],manifest['brief_ref'],supervisor),time.monotonic()+timeout,lambda:False) is not True: return False
        for ref in (manifest['brief_ref'],manifest['content_ref'],manifest['terminal_ref'],record['access_ref']): service.artifacts.get(ref)
        assets=manifest['assets']
        if not isinstance(assets,list) or not assets: return False
        _verify_project(service.artifacts,manifest['project_ref'],len(assets))
        refs,previews,reports=_media_references(service,assets,manifest['brief_ref'],manifest['project_ref'])
        inputs=[manifest_ref,manifest['brief_ref'],manifest['content_ref'],manifest['platform_ref'],manifest['project_ref'],*refs,*previews,*reports]
        inputs,extra=_review_inputs(service,reviewer,run,session,manifest_ref,refs+previews,record['access_ref'],inputs,timeout,lambda:False)
        expected_coverage=_coverage(reviewer,run,session,manifest_ref,refs+previews,record['access_ref'],extra)
        if record.get('review_coverage')!=expected_coverage: return False
        parsed=parse_marketing_result(service.artifacts.get(record['response_ref']).decode('utf-8'),'asset_review')
        if parsed['status']!=record['status'] or parsed['outputs']!=[record['report_ref']] or ('session_id' in parsed and parsed['session_id']!=session): return False
        report=json.loads(service.artifacts.get(record['report_ref']))
        _validate_report(report,refs,parsed,inputs)
        return True
    except Exception: return False


def review(service, run, queue_claim, graph_claim, reviewer, backend):
    graph=service.graph
    if not service.queue.may_start(run,queue_claim): raise OpenFilmStageError('queue claim unavailable')
    timeout=getattr(reviewer,'timeout_seconds',None)
    if type(timeout) is not int or not 1<=timeout<=600 or not all(callable(getattr(reviewer,n,None)) for n in ('ready','review','cancel','verify_access')) or not callable(getattr(backend,'verify_terminal',None)):
        raise OpenFilmStageError('independent asset reviewer unavailable')
    cancelled=lambda:not service.queue.may_start(run,queue_claim)
    try:
        GraphRunner(graph,service.artifacts,lambda *_:None,lambda *_:False,skill_resolver=service.skill_resolver,required_skills=service.required_skills)._verify_upstream_skills(run,'asset_production',graph_claim)
        if _bounded(reviewer.ready,time.monotonic()+timeout,cancelled) is not True: raise ValueError('reviewer unavailable')
        names=service.required_skills['asset_review']
        if not isinstance(names,list) or not names or service.skill_resolver is None: raise ValueError('review skills unavailable')
        resolved=service.skill_resolver.resolve(names)
        skills={name:item['sha256'] for name,item in resolved.items()}
        contents={name:item['content'] for name,item in resolved.items()}
        if any(hashlib.sha256(contents[name].encode('utf-8')).hexdigest()!=skills[name] for name in names): raise ValueError('review skill hash mismatch')
        model=service.model_policy.for_role('asset_review')['model']
        current=graph.get(run,'asset_production')
        if not current or current['status']!='awaiting_review': raise ValueError('review unavailable')
        manifest_ref=current['version']; manifest=json.loads(service.artifacts.get(manifest_ref))
        if manifest['status']!='awaiting_review': raise ValueError('manifest unavailable')
        brief_stage=json.loads(service.artifacts.get(graph.get(run,'asset_brief')['version']))
        if brief_stage['outputs'][0]!=manifest['brief_ref']: raise ValueError('brief changed')
        from runner.openfilm_platform import verify_platform
        verify_platform(service,run,manifest['brief_ref'],json.loads(service.artifacts.get(manifest['brief_ref'])),manifest['platform_ref'],timeout,cancelled)
        supervisor=json.loads(service.artifacts.get(manifest['supervisor_ref']))
        if supervisor['attempt_id']!=manifest['attempt_id'] or supervisor['project_ref']!=manifest['project_ref'] or supervisor['terminal_ref']!=manifest['terminal_ref']: raise ValueError('supervisor binding changed')
        if _bounded(lambda:backend.verify_terminal(run,manifest['attempt_id'],manifest['brief_ref'],supervisor),time.monotonic()+timeout,cancelled) is not True: raise ValueError('render termination unavailable')
        service.artifacts.get(manifest['terminal_ref']); service.artifacts.get(manifest['brief_ref']); service.artifacts.get(manifest['content_ref'])
        assets=manifest['assets']
        if not isinstance(assets,list) or not assets: raise ValueError('assets unavailable')
        _verify_project(service.artifacts,manifest['project_ref'],len(assets))
        refs,previews,reports=_media_references(service,assets,manifest['brief_ref'],manifest['project_ref'])
        inputs=[manifest_ref,manifest['brief_ref'],manifest['content_ref'],manifest['platform_ref'],manifest['project_ref'],*refs,*previews,*reports]
        snapshot=graph.prepare(run,'asset_production',graph_claim)
        if snapshot['current']!=current['revision']: raise ValueError('manifest changed')
        skill_fingerprint=graph.db.execute('SELECT fingerprint FROM graph_skill_state WHERE run=? AND stage=?',(run,'asset_production')).fetchone()
        if not skill_fingerprint: raise ValueError('production skills unavailable')
    except Exception: raise OpenFilmStageError('independent asset review inputs unavailable') from None
    attempt_hash=hashlib.sha256(json.dumps({'manifest':manifest_ref,'skills':skills},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    try: attempt=graph.begin_attempt(run,'asset_production',attempt_hash,graph_claim,2)
    except GraphError as exc: raise OpenFilmStageError(str(exc)) from None
    review_id=hashlib.sha256(json.dumps([run,'asset_review',attempt_hash,attempt],separators=(',',':')).encode()).hexdigest()
    try:
        result=_bounded(lambda:reviewer.review(run=run,attempt_id=review_id,claim=graph_claim,subject=manifest_ref,inputs=inputs,asset_refs=refs,skills=skills,skill_contents=contents,model=model),time.monotonic()+timeout,cancelled)
        if not isinstance(result,dict) or set(result)!={'attempt_id','session_id','response_ref','access_ref'} or result['attempt_id']!=review_id: raise ValueError('review result unavailable')
        session=result['session_id']; response=result['response_ref']; access=result['access_ref']
        if not isinstance(session,str) or not session or not service.provenance.verify(run,'asset_review',session,skills) or not service.provenance.completed(run,'asset_review',session) or not service.provenance.subject_matches(run,'asset_review',session,manifest_ref) or service.provenance.completion_hash(run,'asset_review',session)!=response: raise ValueError('review provenance unavailable')
        parsed=parse_marketing_result(service.artifacts.get(response).decode('utf-8'),'asset_review')
        if 'session_id' in parsed and parsed['session_id']!=session: raise ValueError('review session mismatch')
        service.artifacts.get(access)
        inputs,extra=_review_inputs(service,reviewer,run,session,manifest_ref,refs+previews,access,inputs,timeout,cancelled)
        report=json.loads(service.artifacts.get(parsed['outputs'][0]))
        _validate_report(report,refs,parsed,inputs)
        if cancelled() or _bounded(lambda:backend.verify_terminal(run,manifest['attempt_id'],manifest['brief_ref'],supervisor),time.monotonic()+timeout,cancelled) is not True: raise ValueError('render evidence changed')
        verify_platform(service,run,manifest['brief_ref'],json.loads(service.artifacts.get(manifest['brief_ref'])),manifest['platform_ref'],timeout,cancelled)
        record={'status':parsed['status'],'manifest_ref':manifest_ref,'review_attempt_id':review_id,'review_session':session,'review_skills':skills,'response_ref':response,'access_ref':access,'report_ref':parsed['outputs'][0],'outputs':[]}
        coverage=_coverage(reviewer,run,session,manifest_ref,refs+previews,access,extra)
        if coverage: record['review_coverage']=coverage
        ref=service.artifacts.put(json.dumps(record,sort_keys=True,separators=(',',':')).encode())
        graph.finish(run,'asset_production',snapshot,parsed['status'],ref,graph_claim,completion_input=attempt_hash,skill_fingerprint=skill_fingerprint[0],queue_claim=queue_claim)
        return {'stage':'asset_production','status':parsed['status'],'artifact_ref':ref}
    except Exception:
        try: _bounded(lambda:reviewer.cancel(run,review_id),time.monotonic()+timeout,lambda:False)
        except Exception: pass
        try: service.fence_execution(run)
        except Exception: pass
        raise OpenFilmStageError('asset review outcome requires inspection') from None
