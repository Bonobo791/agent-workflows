import hashlib,json,unittest
from pathlib import Path
from unittest.mock import patch
from runner.local_asset_review import LocalAssetReviewer
from runner.local_scope import LocalScopes
from runner.local_supervisor import LocalSupervisor
from runner.openfilm_stage import produce
from runner.skills import SkillResolver
from tests.test_local_scope import Backend

class LocalAssetReviewTests(unittest.TestCase):
    def test_image_delivery_and_scope_are_required(self):
        from tests.test_openfilm_stage import OpenFilmStageTests
        fixture=OpenFilmStageTests('test_production_ingests_actual_output_but_requires_review');fixture.setUp()
        service=fixture.service;service.state_db_path=str(fixture.root/'state'/'state.db');backend=Backend()
        try:
            skill=fixture.root/'skills'/'asset';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('Review the still image.')
            service.skill_resolver=SkillResolver([skill.parent]);service.required_skills['asset_review']=['asset']
            binary=fixture.root/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            service.model_supervisor=LocalSupervisor(service.state_db_path,str(binary),{'HOME':str(fixture.root)},backend,lambda run:True,lambda run,role:str(fixture.root),lambda:True,{'mcp_servers':[],'plugins':[]},policy_hash='a'*64)
            class Policy:
                def for_role(self,role):return {'model':'synthetic','effort':'medium'}
            service.model_policy=Policy()
            produced=produce(service,'run',fixture.queue,fixture.claim,fixture.backend);subject=produced['artifact_ref'];manifest=json.loads(service.artifacts.get(subject))
            refs=[item['artifact_ref'] for item in manifest['assets']];previews=[p['artifact_ref'] for item in manifest['assets'] for p in item['previews']];reports=[item['look_report_ref'] for item in manifest['assets']]
            inputs=[subject,manifest['brief_ref'],manifest['content_ref'],manifest['platform_ref'],manifest['project_ref'],*refs,*previews,*reports]
            reviewer=LocalAssetReviewer(service);self.assertTrue(reviewer.supports(subject))
            video=dict(manifest);video['assets']=[dict(manifest['assets'][0],kind='mp4')]
            self.assertFalse(reviewer.supports(service.artifacts.put(json.dumps(video).encode())))
            resolved=service.skill_resolver.resolve(['asset']);hashes={k:v['sha256'] for k,v in resolved.items()};contents={k:v['content'] for k,v in resolved.items()}
            expected_images=refs+previews
            class Executor:
                counter=0
                def __init__(self,supervisor,provenance,artifacts,fence,model_policy=None):self.supervisor=supervisor;self.provenance=provenance;self.artifacts=artifacts
                def review(self,**kw):
                    Executor.counter+=1;session='fresh-'+str(Executor.counter)
                    assert kw['image_refs']==expected_images
                    checks={'artifact_ref':refs[0],'content_fidelity':True,'brand_fidelity':True,'rights':True,'platform_suitability':True,'readability':True,'captions':True,'safe_zones':True,'audio':True}
                    report_ref=self.artifacts.put(json.dumps({'assets':[checks]}).encode())
                    output=json.dumps({'stage':'asset_review','status':'passed','input_versions':kw['input_versions'],'outputs':[report_ref],'findings':[]});response_ref=self.artifacts.put(output.encode())
                    self.provenance.bind('run','asset_review',session,kw['skills'],fixture.claim,fresh=True,subject=subject);self.provenance.mark_completed('run','asset_review',session,fixture.claim,response_ref)
                    db=self.provenance.graph.db;db.execute('CREATE TABLE IF NOT EXISTS execution_image_inputs(run TEXT,role TEXT,session TEXT,turn TEXT,image_refs TEXT)')
                    db.execute('INSERT INTO execution_image_inputs VALUES(?,?,?,?,?)',('run','asset_review',session,'turn',json.dumps(kw['image_refs'])))
                    db.execute('CREATE TABLE IF NOT EXISTS completed_turn_outputs(run TEXT,role TEXT,session TEXT,turn TEXT,input_versions TEXT,output_refs TEXT)')
                    db.execute('INSERT INTO completed_turn_outputs VALUES(?,?,?,?,?,?)',('run','asset_review',session,'turn',json.dumps(kw['input_versions']),json.dumps([report_ref])))
                    scopes=LocalScopes(service.state_db_path,backend);unit=scopes.reserve('run');backend.unit=unit;backend.state='active';scopes.bind('run',unit);scopes.stop('run',unit);scopes.close()
                    binding=self.supervisor._binding('run','asset_review',kw['skills'],kw['model'])[1]
                    db.execute('INSERT INTO local_turn_scopes VALUES(?,?,?,?,?,?)',('run','asset_review',session,'turn',unit,binding))
                    return {'output':output,'outputs':[report_ref],'response_ref':response_ref,'session_id':session,'turn_id':'turn'}
            with patch('runner.local_asset_review.CodexExecutor',Executor):
                result=reviewer.review(run='run',attempt_id='attempt',claim=fixture.claim,subject=subject,inputs=inputs,asset_refs=refs,skills=hashes,skill_contents=contents,model='synthetic')
            self.assertTrue(reviewer.verify_access('run','fresh-1',subject,refs+previews,result['access_ref']))
            self.assertFalse(reviewer.verify_access('run','fresh-1',subject,refs,result['access_ref']))
            service.graph.db.execute("UPDATE completed_turn_outputs SET input_versions='[]'")
            self.assertFalse(reviewer.verify_access('run','fresh-1',subject,refs+previews,result['access_ref']))
            service.graph.db.execute('UPDATE completed_turn_outputs SET input_versions=?',(json.dumps(inputs),))
            service.graph.db.execute("UPDATE execution_image_inputs SET image_refs='[]'")
            self.assertFalse(reviewer.verify_access('run','fresh-1',subject,refs+previews,result['access_ref']))
            video_ref=service.artifacts.put(b'synthetic-video');sample=refs[0]
            video=dict(manifest);video['assets']=[dict(manifest['assets'][0],kind='mp4',artifact_ref=video_ref,sha256=video_ref,duration_seconds=1.0,audio_streams=[])]
            subject=service.artifacts.put(json.dumps(video).encode());refs=[video_ref]
            inputs=[subject,video['brief_ref'],video['content_ref'],video['platform_ref'],video['project_ref'],*refs,*previews,*reports]
            class Sampler:
                policy_hash='b'*64
                def sample(self,run,claim,subject,asset):return service.artifacts.put(json.dumps({'sample_ref':sample}).encode())
                def verify(self,run,subject,asset,receipt):return asset==video_ref and json.loads(service.artifacts.get(receipt))['sample_ref']==sample
            reviewer.sampler=Sampler();reviewer.policy_hash=reviewer._policy();expected_images=[sample,*previews]
            self.assertTrue(reviewer.supports(subject))
            with patch('runner.local_asset_review.CodexExecutor',Executor):
                video_result=reviewer.review(run='run',attempt_id='video',claim=fixture.claim,subject=subject,inputs=inputs,asset_refs=refs,skills=hashes,skill_contents=contents,model='synthetic')
            self.assertTrue(reviewer.verify_access('run','fresh-2',subject,refs+previews,video_result['access_ref']))
            self.assertEqual(len(reviewer.extra_inputs('run','fresh-2',subject,refs+previews,video_result['access_ref'])),2)
            video['assets'][0]['audio_streams']=[{'codec':'aac'}]
            subject=service.artifacts.put(json.dumps(video).encode())
            self.assertFalse(reviewer.supports(subject))
            class Transcriber:
                policy_hash='c'*64
                def ready(self):return True
                def supports(self,item):return item.get('audio_streams')==[{'codec':'aac'}]
                def transcribe(self,run,claim,subject,asset,channel):
                    audio=service.artifacts.put(b'audio');transcript=service.artifacts.put(b'{"segments":[]}')
                    return service.artifacts.put(json.dumps({'audio_ref':audio,'transcript_ref':transcript,'coverage':'machine transcript and signal levels; human listening required'}).encode())
                def verify(self,run,subject,asset,channel,receipt):return asset==video_ref and channel==video['assets'][0]['channel']
            reviewer.transcriber=Transcriber();reviewer.policy_hash=reviewer._policy();inputs[0]=subject
            self.assertTrue(reviewer.supports(subject))
            with patch('runner.local_asset_review.CodexExecutor',Executor):
                audio_result=reviewer.review(run='run',attempt_id='audio',claim=fixture.claim,subject=subject,inputs=inputs,asset_refs=refs,skills=hashes,skill_contents=contents,model='synthetic')
            self.assertTrue(reviewer.verify_access('run','fresh-3',subject,refs+previews,audio_result['access_ref']))
            self.assertEqual(len(reviewer.extra_inputs('run','fresh-3',subject,refs+previews,audio_result['access_ref'])),5)
            self.assertIn('human playback and listening',reviewer.coverage('run','fresh-3',subject,refs+previews,audio_result['access_ref']))
            reviewer.transcriber.ready=lambda:False
            self.assertFalse(reviewer.verify_access('run','fresh-3',subject,refs+previews,audio_result['access_ref']))
        finally:fixture.tearDown()
