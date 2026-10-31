import hashlib
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from runner.graph_runner import GraphRunner
from runner.graph import GraphStore, marketing_definition
from runner.openfilm_stage import produce, OpenFilmStageError, _verify_project
from runner.openfilm_review import review, verify_evidence
from runner.service import Service
from runner.stage_dispatch import StageDispatcher, StageError
from tests.source_fixture import REGISTER, verifier
from tests.test_assets import png


class OpenFilmStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.service=Service(self.root/'state'); self.service.create('run','marketing','task'); self.service.enqueue('run')
        self.queue=self.service.queue.claim('worker',60)['token']; self.claim=self.service.graph.claim('run',60)
        store=self.service.artifacts
        self.content=store.put(b'Approved synthetic statement.')
        register=store.put(json.dumps(REGISTER).encode()); proof=store.put(json.dumps(verifier(REGISTER,register)).encode())
        source=store.put(json.dumps({'outputs':[self.content,register],'_source_verification':proof}).encode())
        canonical=store.put(json.dumps({'outputs':[self.content]}).encode())
        frozen=store.put(json.dumps({'content_ref':canonical}).encode())
        self.brief={'content_ref':self.content,'assets':[{'channel':'instagram','kind':'png','width':2,'height':3,'duration_seconds':None,'approved_text':['Approved synthetic statement.'],'claim_ids':['claim-1'],'media':[],'captions':'','alt_text':'Synthetic graphic'}]}
        self.brief_ref=store.put(json.dumps(self.brief).encode()); asset_brief=store.put(json.dumps({'outputs':[self.brief_ref]}).encode())
        graph=self.service.graph
        for stage,ref in [('intake',self.content),('brief',source),('canonical_draft',canonical),('canonical_review',self.content),('freeze',frozen),('asset_brief',asset_brief)]:
            graph.finish('run',stage,graph.prepare('run',stage,self.claim),'passed',ref,self.claim)
        self.render_root=self.root/'render'; self.render_root.mkdir(); (self.render_root/'asset.png').write_bytes(png())
        (self.render_root/'preview.png').write_bytes(png(3,2))
        (self.render_root/'look.json').write_text(json.dumps({'ok':True,'lines':['✓ drawn again: same picture']}))
        project=io.BytesIO()
        with zipfile.ZipFile(project,'w') as archive: archive.writestr('film.html','<html>synthetic film</html>')
        self.project=store.put(project.getvalue()); self.terminal=store.put(b'synthetic terminated process evidence')
        test=self
        class Backend:
            timeout_seconds=5
            def ready(self): return True
            def cancel(self,run,attempt_id): self.cancelled=True
            def execute(self,**kwargs):
                self.calls=getattr(self,'calls',0)+1
                preview={'path':'preview.png','sha256':hashlib.sha256((test.render_root/'preview.png').read_bytes()).hexdigest()}
                report={'path':'look.json','sha256':hashlib.sha256((test.render_root/'look.json').read_bytes()).hexdigest()}
                return {'attempt_id':kwargs['attempt_id'],'status':'exited','root':str(test.render_root),'project_ref':test.project,'outputs':[{'path':'asset.png','sha256':hashlib.sha256((test.render_root/'asset.png').read_bytes()).hexdigest(),'previews':[preview],'report':report}],'terminal_ref':test.terminal}
            def verify_terminal(self,run,attempt_id,brief_ref,result): return run=='run' and brief_ref==test.brief_ref and result['attempt_id']==attempt_id
        self.backend=Backend()
        class PlatformAuthority:
            def check(self,run,brief_ref,brief):
                now=time.time(); source=store.put(b'synthetic platform source')
                items=[{**{key:item[key] for key in ('channel','kind','width','height','duration_seconds')},'source_ref':source,'supported':True} for item in brief['assets']]
                return store.put(json.dumps({'brief_ref':brief_ref,'checked_at':now,'expires_at':now+300,'assets':items}).encode())
            def verify(self,run,brief_ref,brief,proof_ref): return True
        self.service.openfilm_platform_authority=PlatformAuthority()
        self.patch=patch.object(GraphRunner,'_verify_upstream_skills',return_value=None); self.patch.start()
    def tearDown(self): self.patch.stop(); self.service.close(); self.tmp.cleanup()
    def test_production_ingests_actual_output_but_requires_review(self):
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(result['status'],'awaiting_review')
        manifest=json.loads(self.service.artifacts.get(result['artifact_ref']))
        self.assertEqual(self.service.artifacts.get(manifest['assets'][0]['artifact_ref']),png())
        self.assertEqual(manifest['content_ref'],self.content)
        self.assertEqual(self.service.graph.get('run','asset_production')['status'],'awaiting_review')
        with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(self.backend.calls,1)
    def test_production_rejects_repeated_text_cutoff_warning(self):
        (self.render_root/'look.json').write_text(json.dumps({'ok':True,'lines':['! text cut off by its box at 0, 0.5','✓ drawn again: same picture']}))
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(result['status'],'blocked')
        manifest=json.loads(self.service.artifacts.get(result['artifact_ref']))
        self.assertEqual(manifest['reason'],'repeated text cutoff detected')
        self.assertIn('! text cut off',self.service.artifacts.get(manifest['look_report_ref']).decode())
        self.assertEqual(manifest['project_ref'],self.project)
        self.assertTrue(manifest['previews'])
    def test_failed_render_retains_private_visual_failure_report(self):
        report=self.service.artifacts.put(json.dumps({'ok':False,'lines':['✗ font failed to load: synthetic']}).encode())
        execute=self.backend.execute
        def fail(**kwargs):
            result=execute(**kwargs); result['status']='failed'; result['outputs']=[{'look_report_ref':report}]; return result
        self.backend.execute=fail
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        manifest=json.loads(self.service.artifacts.get(result['artifact_ref']))
        self.assertEqual(manifest['status'],'blocked')
        self.assertEqual(manifest['look_report_ref'],report)
    def test_missing_platform_authority_blocks_before_render(self):
        self.service.openfilm_platform_authority=None
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual((result['status'],result['reason']),('blocked','current platform constraints unavailable'))
        self.assertFalse(hasattr(self.backend,'calls'))
    def test_platform_review_timeout_fences_instead_of_recording_a_terminal_block(self):
        self.backend.timeout_seconds=1
        self.service.openfilm_platform_authority.check=lambda *_:time.sleep(1.5)
        with patch.object(self.service,'fence_execution') as fence:
            with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        fence.assert_called_once_with('run')
        self.assertIsNone(self.service.graph.get('run','asset_production'))
        self.assertFalse(hasattr(self.backend,'calls'))
    def test_dispatch_reports_uncertain_platform_review(self):
        from runner.openfilm_stage import PlatformReviewUncertain
        self.service.openfilm_backend=self.backend
        dispatcher=StageDispatcher(self.service)
        with patch.object(dispatcher,'next_stage',return_value='asset_production'),patch('runner.openfilm_stage.produce',side_effect=PlatformReviewUncertain('inspect worker')):
            result=dispatcher.step('run',self.queue,self.claim)
        self.assertEqual(result['status'],'uncertain')
        self.assertIn('inspect',result['reason'])
    def test_verified_platform_block_is_in_review_artifact(self):
        evidence=self.service.artifacts.put(json.dumps({'reason':'Height is unspecified.','findings':[{'description':'Supply a height limit.'}]}).encode())
        self.service.openfilm_platform_authority.check=lambda *_:{'blocked_ref':evidence}
        self.service.openfilm_platform_authority.verify_blocked=lambda *args:args[-1]==evidence
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(result['status'],'blocked')
        artifact=json.loads(self.service.artifacts.get(result['artifact_ref']))
        self.assertEqual(artifact['platform_block_ref'],evidence)
        self.assertEqual(artifact['platform_review']['reason'],'Height is unspecified.')
        self.assertFalse(hasattr(self.backend,'calls'))
    def test_unverified_platform_block_does_not_expose_findings(self):
        evidence=self.service.artifacts.put(json.dumps({'reason':'untrusted'}).encode())
        self.service.openfilm_platform_authority.check=lambda *_:{'blocked_ref':evidence}
        self.service.openfilm_platform_authority.verify_blocked=lambda *_:False
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        artifact=json.loads(self.service.artifacts.get(result['artifact_ref']))
        self.assertEqual(artifact['reason'],'current platform constraints unavailable')
        self.assertNotIn('platform_review',artifact)
    def test_expired_platform_evidence_blocks_before_render(self):
        source=self.service.artifacts.put(b'synthetic source')
        items=[{**{key:item[key] for key in ('channel','kind','width','height','duration_seconds')},'source_ref':source,'supported':True} for item in self.brief['assets']]
        ref=self.service.artifacts.put(json.dumps({'brief_ref':self.brief_ref,'checked_at':1,'expires_at':2,'assets':items}).encode())
        self.service.openfilm_platform_authority.check=lambda *_:ref
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(result['status'],'blocked'); self.assertFalse(hasattr(self.backend,'calls'))
    def test_platform_evidence_requires_matching_asset_and_authenticated_source(self):
        from runner.openfilm_platform import verify_platform
        authority=self.service.openfilm_platform_authority
        proof_ref=authority.check('run',self.brief_ref,self.brief)
        self.assertTrue(verify_platform(self.service,'run',self.brief_ref,self.brief,proof_ref,5,lambda:False))
        proof=json.loads(self.service.artifacts.get(proof_ref)); proof['assets'][0]['width']+=1
        altered=self.service.artifacts.put(json.dumps(proof).encode())
        with self.assertRaises(ValueError): verify_platform(self.service,'run',self.brief_ref,self.brief,altered,5,lambda:False)
        authority.verify=lambda *_:False
        with self.assertRaises(ValueError): verify_platform(self.service,'run',self.brief_ref,self.brief,proof_ref,5,lambda:False)
        authority.verify=lambda *_:True
        source=Path(self.service.artifacts.directory)/json.loads(self.service.artifacts.get(proof_ref))['assets'][0]['source_ref']
        source.unlink()
        with self.assertRaises(Exception): verify_platform(self.service,'run',self.brief_ref,self.brief,proof_ref,5,lambda:False)
    def make_reviewer(self, access=True, safe_zones=True, audio=True):
        service=self.service; claim=self.claim; db_path=str(self.root/'state'/'state.db')
        class Skills:
            def resolve(self,names): return {name:{'sha256':hashlib.sha256(b'synthetic').hexdigest(),'content':'synthetic'} for name in names}
        class Model:
            def for_role(self,role): return {'model':'synthetic','effort':'medium'}
        service.skill_resolver=Skills(); service.required_skills={'asset_review':['role']}; service.model_policy=Model()
        class Reviewer:
            timeout_seconds=5
            def ready(self): return True
            def review(self,*,run,attempt_id,claim,subject,inputs,asset_refs,skills,skill_contents,model):
                from runner.provenance import Provenance
                graph=GraphStore(db_path)
                try:
                    graph.register(marketing_definition()); provenance=Provenance(graph)
                    report={'assets':[{'artifact_ref':ref,'content_fidelity':True,'brand_fidelity':True,'rights':True,'platform_suitability':True,'readability':True,'captions':True,'safe_zones':safe_zones,'audio':audio} for ref in asset_refs]}
                    report_ref=service.artifacts.put(json.dumps(report).encode())
                    output={'stage':'asset_review','status':'passed','input_versions':inputs,'outputs':[report_ref],'findings':[]}
                    response=service.artifacts.put(json.dumps(output).encode()); evidence=service.artifacts.put(b'synthetic independent access receipt')
                    provenance.bind(run,'asset_review','fresh-review',skills,claim,fresh=True,subject=subject)
                    provenance.mark_completed(run,'asset_review','fresh-review',claim,output_hash=response)
                    return {'attempt_id':attempt_id,'session_id':'fresh-review','response_ref':response,'access_ref':evidence}
                finally: graph.close()
            def verify_access(self,run,session,subject,refs,evidence): self.access_refs=refs; return access
            def cancel(self,run,attempt_id): pass
        return Reviewer()
    def test_independent_review_requires_media_access_evidence(self):
        produced=produce(self.service,'run',self.queue,self.claim,self.backend)
        reviewer=self.make_reviewer()
        self.service.openfilm_reviewer=reviewer; self.service.openfilm_backend=self.backend
        result=review(self.service,'run',self.queue,self.claim,reviewer,self.backend)
        self.assertEqual(result['status'],'passed')
        record=json.loads(self.service.artifacts.get(result['artifact_ref']))
        self.assertEqual(record['manifest_ref'],produced['artifact_ref'])
        self.assertEqual(record['review_session'],'fresh-review')
        self.assertTrue(verify_evidence(self.service,'run',record))
        with patch('runner.openfilm_platform.time.time',return_value=time.time()+400): self.assertFalse(verify_evidence(self.service,'run',record))
        self.assertTrue(verify_evidence(self.service,'run',record))
        self.assertEqual(len(reviewer.access_refs),2)
        reviewer.verify_access=lambda *args:False
        self.assertFalse(verify_evidence(self.service,'run',record))
        reviewer.verify_access=lambda *args:True
        manifest=json.loads(self.service.artifacts.get(record['manifest_ref']))
        preview=Path(self.service.artifacts.directory)/manifest['assets'][0]['previews'][0]['artifact_ref']
        preview_bytes=preview.read_bytes(); preview.unlink()
        self.assertFalse(verify_evidence(self.service,'run',record))
        preview.write_bytes(preview_bytes); preview.chmod(0o400)
        asset=Path(self.service.artifacts.directory)/manifest['assets'][0]['artifact_ref']
        asset.unlink()
        self.assertFalse(verify_evidence(self.service,'run',record))
    def test_unverified_media_access_never_passes(self):
        produce(self.service,'run',self.queue,self.claim,self.backend)
        reviewer=self.make_reviewer(access=False)
        with self.assertRaises(OpenFilmStageError): review(self.service,'run',self.queue,self.claim,reviewer,self.backend)
        self.assertEqual(self.service.graph.get('run','asset_production')['status'],'awaiting_review')
        self.assertEqual(self.service.graph.db.execute("SELECT status FROM graph_attempts WHERE stage='asset_production' ORDER BY rowid DESC LIMIT 1").fetchone(),('uncertain',))
    def test_failed_safe_zone_review_cannot_pass(self):
        produce(self.service,'run',self.queue,self.claim,self.backend)
        reviewer=self.make_reviewer(safe_zones=False)
        with self.assertRaises(OpenFilmStageError): review(self.service,'run',self.queue,self.claim,reviewer,self.backend)
        self.assertEqual(self.service.graph.get('run','asset_production')['status'],'awaiting_review')
    def test_failed_audio_review_cannot_pass(self):
        produce(self.service,'run',self.queue,self.claim,self.backend)
        reviewer=self.make_reviewer(audio=False)
        with self.assertRaises(OpenFilmStageError): review(self.service,'run',self.queue,self.claim,reviewer,self.backend)
        self.assertEqual(self.service.graph.get('run','asset_production')['status'],'awaiting_review')
    def test_sampled_video_coverage_is_preserved_and_required(self):
        produce(self.service,'run',self.queue,self.claim,self.backend)
        reviewer=self.make_reviewer();self.service.openfilm_reviewer=reviewer;self.service.openfilm_backend=self.backend
        sample=self.service.artifacts.put(b'synthetic sample');receipt=self.service.artifacts.put(b'synthetic sampling receipt')
        original=reviewer.review
        def with_samples(**kwargs):
            kwargs['inputs']=kwargs['inputs']+[sample,receipt]
            return original(**kwargs)
        reviewer.review=with_samples;reviewer.extra_inputs=lambda *args:[sample,receipt]
        result=review(self.service,'run',self.queue,self.claim,reviewer,self.backend)
        record=json.loads(self.service.artifacts.get(result['artifact_ref']))
        self.assertEqual(record['review_coverage'],'Video review used 12 sampled frames; human playback is required.')
        self.assertTrue(verify_evidence(self.service,'run',record))
        del record['review_coverage']
        self.assertFalse(verify_evidence(self.service,'run',record))
    def test_asset_report_requires_explicit_safe_zone_check(self):
        from runner.openfilm_review import _validate_report
        item={'artifact_ref':'asset','content_fidelity':True,'brand_fidelity':True,'rights':True,'platform_suitability':True,'readability':True,'captions':True,'audio':True}
        parsed={'input_versions':['input'],'status':'passed','outputs':['report'],'findings':[]}
        with self.assertRaises(ValueError): _validate_report({'assets':[item]},['asset'],parsed,['input'])
        item['safe_zones']=True
        _validate_report({'assets':[item]},['asset'],parsed,['input'])
        item['safe_zones']=False
        with self.assertRaises(ValueError): _validate_report({'assets':[item]},['asset'],parsed,['input'])
        item['safe_zones']=True; item['audio']=False
        with self.assertRaises(ValueError): _validate_report({'assets':[item]},['asset'],parsed,['input'])
    def test_asset_report_requires_explicit_audio_check(self):
        from runner.openfilm_review import _validate_report
        item={'artifact_ref':'asset','content_fidelity':True,'brand_fidelity':True,'rights':True,'platform_suitability':True,'readability':True,'captions':True,'safe_zones':True}
        parsed={'input_versions':['input'],'status':'passed','outputs':['report'],'findings':[]}
        with self.assertRaises(ValueError): _validate_report({'assets':[item]},['asset'],parsed,['input'])
    def test_instagram_review_binds_independently_reviewed_media(self):
        produce(self.service,'run',self.queue,self.claim,self.backend)
        reviewer=self.make_reviewer(); service=self.service
        service.openfilm_reviewer=reviewer; service.openfilm_backend=self.backend
        review(service,'run',self.queue,self.claim,reviewer,self.backend)
        service.required_skills['instagram_review']=['role']
        graph=service.graph
        draft=service.artifacts.put(json.dumps({'outputs':[self.content],'session_id':'instagram-writer'}).encode())
        graph.finish('run','instagram_draft',graph.prepare('run','instagram_draft',self.claim),'passed',draft,self.claim)
        graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)')
        graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('run','v1',self.content))
        class Executor:
            def review(self,subject,**kwargs):
                session='instagram-review'; run=kwargs['run']; skills=kwargs['skills']
                output_ref=service.artifacts.put(b'synthetic channel review')
                output=json.dumps({'stage':'instagram_review','status':'passed','input_versions':kwargs['input_versions'],'outputs':[output_ref],'findings':[]})
                response_ref=service.artifacts.put(output.encode())
                service.provenance.bind(run,'instagram_review',session,skills,kwargs['claim'],fresh=True,subject=subject)
                service.provenance.mark_completed(run,'instagram_review',session,kwargs['claim'],output_hash=response_ref)
                proof={'run':run,'stage':'instagram_draft','subject_hash':subject,'reviewer_role':'instagram_review','reviewer_session':session,'skills':skills,'review_output_ref':response_ref}
                return {'session_id':session,'outputs':[output_ref],'output':output,'evidence_ref':service.artifacts.put(json.dumps(proof).encode())}
        service.docker_executor=Executor(); dispatcher=StageDispatcher(service)
        result=dispatcher._model_stage('run','instagram_review',self.queue,self.claim)
        self.assertEqual(result['status'],'passed')
        self.assertTrue(dispatcher._verify_recorded_review('run','instagram_review'))
        asset_record=json.loads(service.artifacts.get(graph.get('run','asset_production')['version']))
        manifest=json.loads(service.artifacts.get(asset_record['manifest_ref']))
        (Path(service.artifacts.directory)/manifest['assets'][0]['artifact_ref']).unlink()
        with self.assertRaises(StageError): dispatcher._verify_recorded_review('run','instagram_review')
    def test_unverified_or_missing_outputs_remain_uncertain_and_cannot_retry(self):
        self.backend.verify_terminal=lambda *args:False
        with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertTrue(self.backend.cancelled)
        row=self.service.graph.db.execute("SELECT status FROM graph_attempts WHERE run='run' AND stage='asset_production'").fetchone()
        self.assertEqual(row,('uncertain',))
        with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(self.backend.calls,1)
    def test_verified_bad_output_blocks_and_retry_limit_is_durable(self):
        (self.render_root/'asset.png').write_bytes(b'invalid image')
        graph=self.service.graph
        for _ in range(2):
            result=produce(self.service,'run',self.queue,self.claim,self.backend)
            self.assertEqual(result['status'],'blocked')
            graph.invalidate('run','asset_production',graph.prepare('run','asset_production',self.claim),self.claim)
        with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(self.backend.calls,2)
    def test_confirmed_render_failure_blocks_without_claiming_output(self):
        execute=self.backend.execute
        def failed(**kwargs):
            result=execute(**kwargs); result['status']='failed'; result['outputs']=[]; return result
        self.backend.execute=failed
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(json.loads(self.service.artifacts.get(result['artifact_ref']))['outputs'],[])
        self.assertEqual(self.service.graph.db.execute("SELECT status FROM graph_attempts WHERE stage='asset_production'").fetchone(),('completed',))
    def test_editable_project_must_be_safe_zip_with_film(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('film.html','synthetic')
            archive.writestr('../escape','unsafe')
        self.project=self.service.artifacts.put(data.getvalue())
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(result['status'],'blocked')
    def test_multiple_assets_require_separate_editable_projects(self):
        self._multiple_assets(None)
    def test_supplied_audio_cannot_pass_with_silent_video(self):
        self._multiple_assets('silent')
    def test_supplied_audio_allows_video_with_decodable_audio(self):
        self._multiple_assets('audible')
    def _multiple_assets(self,audio_mode):
        video=self.render_root/'video.mp4'
        command=['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x48:r=5:d=1']
        if audio_mode=='audible': command+=['-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','aac','-shortest']
        subprocess.run(command+['-c:v','mpeg4',str(video)],check=True,timeout=20)
        media=[]
        if audio_mode:
            wav=self.render_root/'audio.wav'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1',str(wav)],check=True,timeout=20)
            audio=self.service.artifacts.put(wav.read_bytes()); rights=self.service.artifacts.put(b'synthetic audio rights')
            media=[{'artifact_ref':audio,'rights_ref':rights}]
        self.brief['assets'].append({'channel':'instagram','kind':'mp4','width':32,'height':48,'duration_seconds':1,'approved_text':['Approved synthetic statement.'],'claim_ids':['claim-1'],'media':media,'captions':'Synthetic caption','alt_text':'Synthetic video'})
        self.brief_ref=self.service.artifacts.put(json.dumps(self.brief).encode())
        record=self.service.artifacts.put(json.dumps({'outputs':[self.brief_ref]}).encode())
        graph=self.service.graph
        graph.finish('run','asset_brief',graph.prepare('run','asset_brief',self.claim),'passed',record,self.claim)
        project=io.BytesIO()
        with zipfile.ZipFile(project,'w') as archive:
            archive.writestr('projects/0/film.html','synthetic still')
            archive.writestr('projects/1/film.html','synthetic video')
        self.project=self.service.artifacts.put(project.getvalue())
        with self.assertRaises(ValueError): _verify_project(self.service.artifacts,self.project,3)
        execute=self.backend.execute
        def render(**kwargs):
            result=execute(**kwargs)
            result['outputs'].append({'path':'video.mp4','sha256':hashlib.sha256(video.read_bytes()).hexdigest(),'previews':result['outputs'][0]['previews'],'report':result['outputs'][0]['report']})
            return result
        self.backend.execute=render
        result=produce(self.service,'run',self.queue,self.claim,self.backend)
        manifest=json.loads(self.service.artifacts.get(result['artifact_ref']))
        if audio_mode=='silent':
            self.assertEqual(result['status'],'blocked')
            self.assertEqual(manifest['reason'],'supplied audio missing from video')
            return
        self.assertEqual([item['kind'] for item in manifest['assets']],['png','mp4'])
        self.assertEqual(result['status'],'awaiting_review')
    def test_docker_backend_runs_fixed_networkless_render_and_verifies_identity(self):
        self._docker_backend_render(False)
    def test_docker_backend_retains_failed_look_evidence(self):
        self._docker_backend_render(True)
    def _docker_backend_render(self,visual_failure):
        from runner.docker_config import DockerConfig
        from runner.docker_controller import DockerAssignments
        from runner.docker_supervisor import DockerSupervisor
        from runner.docker_openfilm import DockerOpenFilmBackend
        jobs=self.root/'state'/'jobs'; jobs.mkdir(mode=0o700)
        config=DockerConfig({'socket':'/private/engine.sock','image':'example@sha256:'+'1'*64,'network':'aw-test','job_root':str(jobs)},self.root/'state')
        test=self
        class Engine:
            def __init__(self): self.spec=None; self.state='created'; self.cid='a'*64; self.exit_code=0; self.cancel_on_start=False
            def request(self,method,path,body=None):
                if path=='/containers/create': self.spec=body; return 201,{'Id':self.cid}
                if path.endswith('/json'):
                    host={**self.spec['HostConfig'],'Privileged':False,'CapAdd':None}
                    cfg={'Labels':self.spec['Labels'],'Image':self.spec['Image'],'Entrypoint':self.spec['Entrypoint'],'Cmd':self.spec['Cmd'],'User':self.spec['User'],'Env':self.spec['Env']}
                    return 200,{'Id':self.cid,'Config':cfg,'HostConfig':host,'State':{'Running':self.state=='running','Status':self.state,'ExitCode':self.exit_code}}
                if path.endswith('/start'):
                    if self.cancel_on_start:
                        backend.cancel('run',self.spec['Labels']['agent-workflows.run'][3:])
                        self.state='running'; return 204,None
                    workspace=next(m['Source'] for m in self.spec['HostConfig']['Mounts'] if m['Target']=='/workspace')
                    output=next(m['Source'] for m in self.spec['HostConfig']['Mounts'] if m['Target']=='/output')
                    assert (Path(workspace)/'project'/'film.html').is_file()
                    if visual_failure:
                        Path(output,'look-failure.json').write_text(json.dumps({'ok':False,'lines':['✗ synthetic missing font']}))
                        self.exit_code=1; self.state='exited'; return 204,None
                    Path(output,'asset.png').write_bytes(png()); Path(output,'preview.png').write_bytes(png(3,2))
                    Path(output,'look.json').write_text(json.dumps({'ok':True,'lines':['✓ same picture']}))
                    def entry(name): return {'path':name,'sha256':hashlib.sha256(Path(output,name).read_bytes()).hexdigest()}
                    asset=entry('asset.png'); asset.update(previews=[entry('preview.png')],report=entry('look.json'))
                    Path(output,'render-result.json').write_text(json.dumps({'outputs':[asset]}))
                    self.state='exited'; return 204,None
                if '/stop?' in path: self.state='exited'; self.exit_code=137; return 204,None
                if '/wait?' in path: return 200,{'StatusCode':self.exit_code}
                raise AssertionError(path)
        class Builder:
            def ready(self): return True
            def supports(self,brief): return True
            def build(self,**kwargs): return {'project_ref':test.project,'proof_ref':test.terminal}
            def verify(self,run,attempt_id,brief_ref,project_ref,proof_ref): return run=='run' and brief_ref==test.brief_ref and project_ref==test.project and proof_ref==test.terminal
            def cancel(self,run,attempt_id): pass
        engine=Engine(); assignments=DockerAssignments(str(self.root/'state'/'state.db'),engine,config.image)
        supervisor=DockerSupervisor(assignments,config,lambda run:True)
        self.service.docker_supervisor=supervisor
        backend=DockerOpenFilmBackend(supervisor,self.service.artifacts,Builder(),lambda policy:True,timeout_seconds=10)
        self.service.openfilm_backend=backend
        from runner.control import ControlPlane
        from runner.service import ServiceError
        control=ControlPlane(self.service,'synthetic-secret')
        self.assertEqual(control.handle('synthetic-secret',{'action':'openfilm_policy'})['policy_hash'],backend.policy_hash)
        with self.assertRaises(ServiceError): self.service.approve('controller','openfilm_policy','wrong','operator')
        self.assertFalse(backend.ready())
        self.service.approve('controller','docker_config',config.policy_hash,'operator')
        self.assertFalse(backend.ready())
        self.service.approve('controller','openfilm_policy',backend.policy_hash,'operator')
        try:
            with patch.object(backend,'_policy',return_value='changed'):
                self.assertFalse(backend.ready())
            graph=self.service.graph
            count=graph.begin_attempt('run','asset_production','probe',self.claim,2)
            attempt=hashlib.sha256(json.dumps(['run','asset_production','probe',count],separators=(',',':')).encode()).hexdigest()
            self.assertFalse(backend._active(graph.db,'run',attempt,'wrong',self.queue))
            self.assertFalse(backend._active(graph.db,'run',attempt,self.claim,'wrong'))
            self.assertTrue(backend._active(graph.db,'run',attempt,self.claim,self.queue))
            graph.resolve_attempt('run','asset_production','probe',self.claim,'not_executed')
            result=produce(self.service,'run',self.queue,self.claim,backend)
            if visual_failure:
                self.assertEqual(result['status'],'blocked')
                manifest=json.loads(self.service.artifacts.get(result['artifact_ref']))
                self.assertIn('synthetic missing font',self.service.artifacts.get(manifest['look_report_ref']).decode())
                self.assertEqual(manifest['project_ref'],self.project)
                return
            self.assertEqual(result['status'],'awaiting_review')
            manifest=json.loads(self.service.artifacts.get(result['artifact_ref']))
            receipt=json.loads(self.service.artifacts.get(manifest['supervisor_ref']))
            self.assertTrue(backend.verify_terminal('run',manifest['attempt_id'],self.brief_ref,receipt))
            original_env=engine.spec['Env'][:]
            engine.spec['Env']=[item if not item.startswith('PLAYWRIGHT_BROWSERS_PATH=') else 'PLAYWRIGHT_BROWSERS_PATH=/unexpected' for item in original_env]
            self.assertFalse(backend.verify_terminal('run',manifest['attempt_id'],self.brief_ref,receipt))
            engine.spec['Env']=original_env+['PLAYWRIGHT_BROWSERS_PATH=/unexpected']
            self.assertFalse(backend.verify_terminal('run',manifest['attempt_id'],self.brief_ref,receipt))
            engine.spec['Env']=original_env
            self.assertEqual(engine.spec['HostConfig']['NetworkMode'],'none')
            workspace=next(item['Source'] for item in engine.spec['HostConfig']['Mounts'] if item['Target']=='/workspace')
            job_input=Path(workspace,'brief.json').read_text()
            self.assertNotIn(self.claim,job_input); self.assertNotIn(self.queue,job_input)
            engine.spec['HostConfig']['NetworkMode']='aw-test'
            self.assertFalse(backend.verify_terminal('run',manifest['attempt_id'],self.brief_ref,receipt))
            graph=self.service.graph
            graph.invalidate('run','asset_production',graph.prepare('run','asset_production',self.claim),self.claim)
            engine.cid='b'*64; engine.state='created'; engine.exit_code=0; engine.cancel_on_start=True
            with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,backend)
            self.assertEqual(engine.state,'exited')
            self.assertEqual(graph.db.execute("SELECT status FROM graph_attempts WHERE stage='asset_production' AND status='uncertain'").fetchone(),('uncertain',))
        finally: assignments.close(); self.service.docker_supervisor=None
    def test_backend_failure_requests_cancel_and_leaves_uncertain_attempt(self):
        def fail(**kwargs): raise RuntimeError('transport failure')
        self.backend.execute=fail
        with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertTrue(self.backend.cancelled)
        self.assertEqual(self.service.graph.db.execute("SELECT status FROM graph_attempts WHERE stage='asset_production'").fetchone(),('uncertain',))
    def test_result_from_another_attempt_is_not_accepted(self):
        execute=self.backend.execute
        def replay(**kwargs):
            result=execute(**kwargs); result['attempt_id']='f'*64; return result
        self.backend.execute=replay
        with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertEqual(self.service.graph.db.execute("SELECT status FROM graph_attempts WHERE stage='asset_production'").fetchone(),('uncertain',))
    def test_unavailable_backend_is_an_explicit_blocked_state(self):
        self.service.openfilm_backend=self.backend
        self.backend.ready=lambda:False
        dispatcher=StageDispatcher(self.service)
        with patch.object(dispatcher,'next_stage',return_value='asset_production'):
            result=dispatcher.step('run',self.queue,self.claim)
        self.assertEqual(result,{'stage':'asset_production','status':'blocked','reason':'isolated render supervisor unavailable'})
        self.assertEqual(self.service.graph.db.execute("SELECT COUNT(*) FROM graph_attempts WHERE stage='asset_production'").fetchone(),(0,))
    def test_dispatch_waits_for_media_or_runs_configured_reviewer(self):
        produce(self.service,'run',self.queue,self.claim,self.backend)
        graph=self.service.graph
        for channel in ('linkedin','x','facebook','reddit'):
            for suffix in ('_draft','_review'):
                stage=channel+suffix
                graph.finish('run',stage,graph.prepare('run',stage,self.claim),'passed',self.content,self.claim)
        graph.finish('run','instagram_draft',graph.prepare('run','instagram_draft',self.claim),'passed',self.content,self.claim)
        dispatcher=StageDispatcher(self.service)
        self.assertEqual(dispatcher.next_stage('run',self.claim),'awaiting_review')
        self.service.openfilm_backend=self.backend; self.service.openfilm_reviewer=self.make_reviewer()
        self.assertEqual(dispatcher.next_stage('run',self.claim),'asset_review')
        self.service.openfilm_reviewer.supports=lambda subject:False
        self.assertEqual(dispatcher.next_stage('run',self.claim),'awaiting_review')
    def test_cancellation_requests_stop_and_never_marks_render_complete(self):
        started=threading.Event()
        def running(**kwargs):
            started.set(); time.sleep(.3); return {}
        self.backend.execute=running
        self.service.queue.may_start=lambda run,token:not started.is_set()
        with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertTrue(self.backend.cancelled)
        self.assertIsNone(self.service.graph.get('run','asset_production'))
        self.assertEqual(self.service.graph.db.execute("SELECT status FROM graph_attempts WHERE stage='asset_production'").fetchone(),('uncertain',))
    def test_changed_brief_cannot_be_committed_after_render(self):
        execute=self.backend.execute
        changed=self.service.artifacts.put(b'changed asset brief')
        db_path=str(self.root/'state'/'state.db')
        def stale(**kwargs):
            graph=GraphStore(db_path)
            try:
                graph.register(marketing_definition())
                graph.finish('run','asset_brief',graph.prepare('run','asset_brief',self.claim),'passed',changed,self.claim)
            finally: graph.close()
            return execute(**kwargs)
        self.backend.execute=stale
        with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertIsNone(self.service.graph.get('run','asset_production'))
        self.assertEqual(self.service.graph.db.execute("SELECT status FROM graph_attempts WHERE stage='asset_production'").fetchone(),('uncertain',))
    def test_expired_queue_claim_cannot_commit_output(self):
        from workflows.marketing.assets import ingest_verified_asset
        def expire(*args,**kwargs):
            result=ingest_verified_asset(*args,**kwargs)
            self.service.queue.db.execute("UPDATE jobs SET expires=0 WHERE run='run'")
            return result
        with patch('runner.openfilm_stage.ingest_verified_asset',side_effect=expire):
            with self.assertRaises(OpenFilmStageError): produce(self.service,'run',self.queue,self.claim,self.backend)
        self.assertIsNone(self.service.graph.get('run','asset_production'))
        self.assertEqual(self.service.graph.db.execute("SELECT status FROM graph_attempts WHERE stage='asset_production'").fetchone(),('uncertain',))
