"""Claim-bound OpenFilm production using an explicitly trusted supervisor."""
import hashlib
import io
import json
import stat
import time
import zipfile
from pathlib import PurePosixPath
from runner.artifacts import ArtifactError
from runner.codex_executor import _bounded
from runner.graph import GraphError
from runner.graph_runner import GraphRunner
from runner.source_register import validate as validate_sources
from workflows.marketing.assets import ingest_verified_asset, ingest_verified_preview
from workflows.marketing.adapters import MarketingAdapter, AdapterError
from workflows.marketing.openfilm_brief import validate_brief


class OpenFilmStageError(RuntimeError): pass
class TextCutoffError(OpenFilmStageError): pass
class MissingAudioError(OpenFilmStageError): pass


def _verify_project(store, ref, asset_count):
    data=store.get(ref)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries=archive.infolist()
        if not entries or len(entries)>10000 or sum(item.file_size for item in entries)>100*1024*1024: raise ValueError('invalid project size')
        names=set()
        for item in entries:
            path=PurePosixPath(item.filename)
            mode=item.external_attr >> 16
            parts=item.filename.rstrip('/').split('/')
            if not item.filename or '\\' in item.filename or ':' in item.filename or path.is_absolute() or any(part in ('','.','..') for part in parts) or item.filename in names or item.flag_bits & 1 or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))): raise ValueError('unsafe project entry')
            names.add(item.filename)
        required=['film.html'] if asset_count==1 else [f'projects/{index}/film.html' for index in range(asset_count)]
        if any(name not in names or archive.getinfo(name).file_size==0 for name in required) or archive.testzip() is not None: raise ValueError('editable film unavailable')


def produce(service, run, queue_claim, graph_claim, backend):
    graph=service.graph
    if not service.queue.may_start(run,queue_claim): raise OpenFilmStageError('queue claim unavailable')
    if not all(callable(getattr(backend,name,None)) for name in ('ready','execute','cancel','verify_terminal')): raise OpenFilmStageError('isolated render supervisor unavailable')
    timeout=getattr(backend,'timeout_seconds',None)
    if type(timeout) is not int or not 1<=timeout<=600: raise OpenFilmStageError('bounded render timeout required')
    try: ready=_bounded(backend.ready,time.monotonic()+timeout,lambda:not service.queue.may_start(run,queue_claim))
    except Exception: raise OpenFilmStageError('isolated render supervisor unavailable') from None
    if ready is not True: raise OpenFilmStageError('isolated render supervisor unavailable')
    runner=GraphRunner(graph,service.artifacts,lambda *_:None,lambda *_:False,skill_resolver=service.skill_resolver,required_skills=service.required_skills)
    runner._verify_upstream_skills(run,'asset_production',graph_claim)
    names=service.required_skills.get('asset_production',[])
    if names and not service.skill_resolver: raise OpenFilmStageError('required skills unavailable')
    skills=service.skill_resolver.resolve(names) if names else {}
    fingerprint=hashlib.sha256(json.dumps({k:v['sha256'] for k,v in skills.items()},sort_keys=True).encode()).hexdigest()
    snapshot=graph.prepare(run,'asset_production',graph_claim)
    if graph.get(run,'asset_production') is not None: raise OpenFilmStageError('existing asset outcome requires review or explicit invalidation')
    try:
        brief_stage=graph.get(run,'asset_brief'); frozen=graph.get(run,'freeze'); source=graph.get(run,'brief')
        record=json.loads(service.artifacts.get(brief_stage['version']))
        brief_ref=record['outputs'][0]; brief=json.loads(service.artifacts.get(brief_ref))
        canonical=json.loads(service.artifacts.get(json.loads(service.artifacts.get(frozen['version']))['content_ref']))
        content_ref=canonical['outputs'][0]
        source_record=json.loads(service.artifacts.get(source['version']))
        register_ref=source_record['outputs'][1]
        validate_sources(service.artifacts,register_ref,source_record['_source_verification'])
        validate_brief(service.artifacts,brief,content_ref,register_ref)
        canonical_text=service.artifacts.get(content_ref).decode('utf-8')
    except Exception: raise OpenFilmStageError('asset brief or approved source unavailable') from None
    if callable(getattr(backend,'blocker',None)):
        try: blocker=_bounded(lambda:backend.blocker(brief),time.monotonic()+timeout,lambda:not service.queue.may_start(run,queue_claim))
        except Exception: raise OpenFilmStageError('render capability unavailable') from None
        if blocker is not None:
            if blocker not in ('supplied media unavailable or unsupported','project builder does not support this brief'): raise OpenFilmStageError('render capability unavailable')
            ref=service.artifacts.put(json.dumps({'status':'blocked','reason':blocker,'outputs':[]},sort_keys=True).encode())
            graph.finish(run,'asset_production',snapshot,'blocked',ref,graph_claim,skill_fingerprint=fingerprint,queue_claim=queue_claim)
            return {'stage':'asset_production','status':'blocked','artifact_ref':ref,'reason':blocker}
    cancelled=lambda:not service.queue.may_start(run,queue_claim)
    try:
        from runner.openfilm_platform import verify_platform
        authority=service.openfilm_platform_authority
        if authority is None: raise ValueError('platform authority unavailable')
        if callable(getattr(authority,'check_for_claim',None)):
            platform_ref=_bounded(lambda:authority.check_for_claim(run,brief_ref,brief,graph_claim,queue_claim),time.monotonic()+timeout,cancelled)
        elif callable(getattr(authority,'check',None)):
            platform_ref=_bounded(lambda:authority.check(run,brief_ref,brief),time.monotonic()+timeout,cancelled)
        else: raise ValueError('platform authority unavailable')
        verify_platform(service,run,brief_ref,brief,platform_ref,timeout,cancelled)
    except Exception:
        ref=service.artifacts.put(json.dumps({'status':'blocked','reason':'current platform constraints unavailable','outputs':[]},sort_keys=True).encode())
        graph.finish(run,'asset_production',snapshot,'blocked',ref,graph_claim,skill_fingerprint=fingerprint,queue_claim=queue_claim)
        return {'stage':'asset_production','status':'blocked','artifact_ref':ref,'reason':'current platform constraints unavailable'}
    input_hash=hashlib.sha256(json.dumps({'brief':brief_ref,'snapshot':snapshot,'skills':fingerprint},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    try: attempt=graph.begin_attempt(run,'asset_production',input_hash,graph_claim,2)
    except GraphError as exc: raise OpenFilmStageError(str(exc)) from None
    attempt_id=hashlib.sha256(json.dumps([run,'asset_production',input_hash,attempt],separators=(',',':')).encode()).hexdigest()
    def cancelled(): return not service.queue.may_start(run,queue_claim)
    terminated=False
    try:
        result=_bounded(lambda:backend.execute(run=run,attempt_id=attempt_id,brief_ref=brief_ref,brief=brief,canonical=canonical_text,graph_claim=graph_claim,queue_claim=queue_claim),time.monotonic()+timeout,cancelled)
        if not isinstance(result,dict) or set(result)!={'attempt_id','status','root','project_ref','outputs','terminal_ref'} or result['attempt_id']!=attempt_id or result['status'] not in ('exited','failed'): raise OpenFilmStageError('render outcome uncertain')
        verified=_bounded(lambda:backend.verify_terminal(run,attempt_id,brief_ref,result),time.monotonic()+timeout,cancelled)
        if cancelled() or verified is not True: raise OpenFilmStageError('render termination unverified')
        terminated=True
        service.artifacts.get(result['terminal_ref'])
        supervisor_ref=service.artifacts.put(json.dumps(result,sort_keys=True,separators=(',',':')).encode())
        if result['status']=='failed':
            manifest={'status':'blocked','reason':'render failed','brief_ref':brief_ref,'attempt_id':attempt_id,'terminal_ref':result['terminal_ref'],'outputs':[]}
            try:
                failure=result['outputs']
                if isinstance(failure,list) and len(failure)==1 and isinstance(failure[0],dict) and set(failure[0])=={'look_report_ref'}:
                    report_ref=failure[0]['look_report_ref']; report=json.loads(service.artifacts.get(report_ref))
                    if isinstance(report,dict) and set(report)=={'ok','lines'} and report['ok'] is False and isinstance(report['lines'],list) and all(isinstance(line,str) for line in report['lines']):
                        manifest.update(look_report_ref=report_ref,project_ref=result['project_ref'])
            except (TypeError,ValueError,KeyError,ArtifactError): pass
            status='blocked'
        else:
            try:
                verify_platform(service,run,brief_ref,brief,platform_ref,timeout,cancelled)
                _verify_project(service.artifacts,result['project_ref'],len(brief['assets']))
                if not isinstance(result['outputs'],list) or len(result['outputs'])!=len(brief['assets']): raise OpenFilmStageError('render outputs incomplete')
                assets=[]
                for spec,output in zip(brief['assets'],result['outputs']):
                    if not isinstance(output,dict) or set(output)!={'path','sha256','previews','report'}: raise OpenFilmStageError('render output invalid')
                    checked=ingest_verified_asset(service.artifacts,result['root'],output['path'],{**spec,'version':brief_ref},{'brief_version':brief_ref,'project_version':result['project_ref'],'asset_sha256':output['sha256']},result['project_ref'])
                    if checked.get('status')!='awaiting_review': raise OpenFilmStageError('render output failed technical verification')
                    if spec['kind']=='mp4':
                        from runner.openfilm_builder import _media_path
                        if any(_media_path(service.artifacts.get(item['artifact_ref']),item['artifact_ref']).endswith(('.wav','.mp3')) for item in spec['media']) and not checked['audio_streams']: raise MissingAudioError()
                    previews=output['previews']
                    if not isinstance(previews,list) or not 1<=len(previews)<=10: raise OpenFilmStageError('preview unavailable')
                    preview_refs=[]
                    for preview in previews:
                        if not isinstance(preview,dict) or set(preview)!={'path','sha256'}: raise OpenFilmStageError('preview invalid')
                        item=ingest_verified_preview(service.artifacts,result['root'],preview['path'],preview['sha256'],brief_ref,result['project_ref'])
                        if item.get('status')=='blocked': raise OpenFilmStageError('preview failed verification')
                        preview_refs.append(item)
                    report=output['report']
                    if not isinstance(report,dict) or set(report)!={'path','sha256'}: raise OpenFilmStageError('visual report unavailable')
                    report_ref=MarketingAdapter(service.artifacts,None).ingest(result['root'],report['path'],max_bytes=1048576)
                    if report_ref!=report['sha256']: raise OpenFilmStageError('visual report changed')
                    report_data=json.loads(service.artifacts.get(report_ref))
                    if not isinstance(report_data,dict) or set(report_data)!={'ok','lines'} or report_data['ok'] is not True or not isinstance(report_data['lines'],list) or any(not isinstance(line,str) for line in report_data['lines']) or not any(line.startswith('✓ ') and 'same picture' in line for line in report_data['lines']): raise OpenFilmStageError('visual check unavailable')
                    if any(line.startswith('! text cut off ') for line in report_data['lines']): raise TextCutoffError()
                    assets.append({'channel':spec['channel'],'kind':spec['kind'],**checked,'previews':preview_refs,'look_report_ref':report_ref})
                manifest={'status':'awaiting_review','brief_ref':brief_ref,'content_ref':content_ref,'platform_ref':platform_ref,'attempt_id':attempt_id,'project_ref':result['project_ref'],'terminal_ref':result['terminal_ref'],'supervisor_ref':supervisor_ref,'assets':assets,'outputs':[]}
                status='awaiting_review'
            except MissingAudioError:
                manifest={'status':'blocked','reason':'supplied audio missing from video','brief_ref':brief_ref,'attempt_id':attempt_id,'terminal_ref':result['terminal_ref'],'project_ref':result['project_ref'],'outputs':[]}
                status='blocked'
            except TextCutoffError:
                manifest={'status':'blocked','reason':'repeated text cutoff detected','brief_ref':brief_ref,'attempt_id':attempt_id,'terminal_ref':result['terminal_ref'],'project_ref':result['project_ref'],'look_report_ref':report_ref,'previews':preview_refs,'outputs':[]}
                status='blocked'
            except (ValueError,KeyError,TypeError,OSError,zipfile.BadZipFile,ArtifactError,AdapterError,OpenFilmStageError):
                manifest={'status':'blocked','reason':'render output or project failed technical verification','brief_ref':brief_ref,'attempt_id':attempt_id,'terminal_ref':result['terminal_ref'],'outputs':[]}
                status='blocked'
        ref=service.artifacts.put(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode())
        graph.finish(run,'asset_production',snapshot,status,ref,graph_claim,completion_input=input_hash,skill_fingerprint=fingerprint,queue_claim=queue_claim)
        return {'stage':'asset_production','status':status,'artifact_ref':ref}
    except Exception:
        if not terminated:
            try: _bounded(lambda:backend.cancel(run,attempt_id),time.monotonic()+timeout,lambda:False)
            except Exception: pass
        try: service.fence_execution(run)
        except Exception: pass
        raise OpenFilmStageError('render outcome requires inspection') from None
