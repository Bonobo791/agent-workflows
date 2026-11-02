"""Opt-in signed-in graph dispatch using a public, non-campaign fixture."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest
from tests.source_fixture import verifier as fixture_verifier


@unittest.skipUnless((os.environ.get('WORKFLOW_TEST_PUBLIC_SOURCE')=='1' or os.environ.get('WORKFLOW_TEST_SYNTHETIC_SOURCE')=='1') and os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1' and os.environ.get('CODEX_TEST_EXECUTABLE'),'signed-in marketing probe not configured')
class LocalMarketingGraphLiveTests(unittest.TestCase):
    def test_fixture_reaches_freeze_approval_gate(self):
        from runner.codex_executor import CodexExecutor
        from runner.local_config_inspect import inspect_config
        from runner.local_process import start_local_process
        from runner.local_scope import SystemdScopes, unresolved_scope_rows
        from runner.manual_lifecycle import ManualLifecycle
        from runner.model_policy import ModelPolicy
        from runner.protocol import AppServer
        from runner.service import Service
        from runner.skills import SkillResolver
        from runner.source_fetch import SourceFetcher
        from runner.source_reviewer import SourceReviewer
        from runner.source_snapshots import SourceSnapshots
        from runner.stage_dispatch import StageDispatcher
        from runner.worker import manual_fence_ready

        temporary=tempfile.TemporaryDirectory(prefix='workflow-marketing-graph-live-',delete=False)
        root=Path(temporary.name); workspace=root/'workspace'; workspace.mkdir(mode=0o700)
        skills=root/'skills'; skills.mkdir(mode=0o700)
        synthetic_source=os.environ.get('WORKFLOW_TEST_SYNTHETIC_SOURCE')=='1'
        source_url='https://example.com/'
        for name,content in {
            'brief':'Prepare only the requested fixture brief and exact candidate source register. Do not invent claims.',
            'writer':'Write the requested note using only supplied verified evidence. Include the required heading and each requested supported statement; do not return only a heading.',
            'editor':('Review the draft against the supplied source register and page rules. This offline synthetic fixture uses verifier_id synthetic-independent-verifier as its declared test authority and intentionally does not fetch the public URL; check that the draft has the required heading and only registered claims. Report actual material problems only.' if synthetic_source else 'Review the assigned draft against the supplied source and rules. Report actual material problems only.'),
            'source':'Review supplied source snapshots. A page is primary evidence of its own HTML title.',
            'channel-writer':'Adapt the supplied frozen content for the assigned channel. Use supplied source_verification and source_snapshots; do not request duplicate evidence when a verified proof is present. Do not introduce new claims or publish.',
            'channel-editor':('Independently review the assigned channel draft against its supplied source verification and OpenFilm asset review. For this offline synthetic fixture, trust verifier_id synthetic-independent-verifier as the source authority; compare the exact draft, channel-bound brief asset and passed per-asset checks. The asset reviewer inspected the actual media. Reddit still requires a real community and rules. Report actual material problems only.' if synthetic_source else 'Independently review the assigned channel draft against its supplied source verification, snapshots and OpenFilm asset review. Compare the exact draft, channel-bound brief asset and passed per-asset checks; the asset reviewer inspected the actual media. Report actual material problems only.'),
        }.items():
            directory=skills/name; directory.mkdir(mode=0o700); (directory/'SKILL.md').write_text(content)
        render=os.environ.get('WORKFLOW_TEST_RENDER')=='1'
        platform_source=os.environ.get('WORKFLOW_TEST_PLATFORM_SOURCE')
        if platform_source: self.assertTrue(render,'source-backed platform probe requires rendering configuration')
        if os.environ.get('WORKFLOW_TEST_DECISION')=='1': self.assertEqual(os.environ.get('WORKFLOW_TEST_CHANNELS'),'1','decision probe requires the channel workflow')
        if render:
            self.assertEqual(os.environ.get('WORKFLOW_TEST_CHANNELS'),'1','render probe requires the channel workflow')
            manual=Path(os.environ['OPENFILM_TEST_MANUAL']).read_text()
            directory=skills/'openfilm'; directory.mkdir(mode=0o700); (directory/'SKILL.md').write_text(manual)
            directory=skills/'asset-editor'; directory.mkdir(mode=0o700); (directory/'SKILL.md').write_text('Independently inspect the supplied fixture images and evidence. Do not infer missing evidence.')
            if platform_source:
                directory=skills/'platform-editor'; directory.mkdir(mode=0o700); (directory/'SKILL.md').write_text('Compare the exact requested asset to the supplied official source. Block if any requirement is not explicitly established.')
        backend=SystemdScopes(os.environ)
        service=Service(str(root)); service.state_db_path=str(root/'state.db')
        service.lifecycle=ManualLifecycle(service.state_db_path,'fixture','fixture-incarnation'); completed=False
        try:
            config=inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],str(workspace),os.environ)
            discovery=start_local_process(str(root/'state.db'),'discovery',str(workspace),config['codex_executable'],os.environ,backend,lambda:True,config['disabled_integrations'])
            try:
                app=AppServer(discovery.open_transport(time.monotonic()+90,lambda:False)); app.initialize()
                self.assertTrue(discovery.verify_config(app._request)); self.assertIsInstance(app._request('account/read',{'refreshToken':False}).get('account'),dict)
                policy=ModelPolicy(); policy.discover(app._request)
            finally: discovery.finish()
            from runner.local_config import local_supervisor_factory
            supervisor=local_supervisor_factory(config,os.environ,str(root))(service)
            supervisor.fence=lambda run:manual_fence_ready(service,run)
            service.model_supervisor=supervisor; service.model_policy=policy
            service.model_executor=CodexExecutor(supervisor,service.provenance,service.artifacts,service.fence_execution,model_policy=policy)
            service.skill_resolver=SkillResolver([str(skills)])
            service.required_skills={'brief':['brief'],'canonical_draft':['writer'],'canonical_review':['editor'],'source_review':['source']}
            if os.environ.get('WORKFLOW_TEST_CHANNELS')=='1':
                service.required_skills.update({name+'_draft':['channel-writer'] for name in ('linkedin','x','facebook','instagram','reddit')})
                service.required_skills.update({name+'_review':['channel-editor'] for name in ('linkedin','x','facebook','instagram','reddit')})
                service.required_skills['asset_brief']=['channel-writer']
            if render:
                from runner.docker_config import DockerConfig
                from runner.docker_controller import DockerAssignments, UnixEngine
                from runner.docker_openfilm import DockerOpenFilmBackend
                from runner.docker_supervisor import DockerSupervisor
                from runner.local_asset_review import LocalAssetReviewer
                from runner.openfilm_builder import CodexOpenFilmProjectBuilder
                from runner.podman_policy import PodmanRenderPolicy
                jobs=root/'jobs'; jobs.mkdir(mode=0o700)
                runtime=DockerConfig({'socket':os.environ['OPENFILM_PODMAN_SOCKET'],'image':os.environ['OPENFILM_PODMAN_IMAGE'],'network':'none','job_root':str(jobs),'engine':'podman-rootless'},root)
                service.docker_assignments=DockerAssignments(str(root/'state.db'),UnixEngine(runtime.socket),runtime.image)
                service.docker_supervisor=DockerSupervisor(service.docker_assignments,runtime,lambda run:manual_fence_ready(service,run))
                service.required_skills.update({'asset_production':['openfilm'],'asset_review':['asset-editor']})
                verifier=PodmanRenderPolicy(runtime,os.environ)
                builder=CodexOpenFilmProjectBuilder(service,verifier,timeout_seconds=300)
                service.openfilm_backend=DockerOpenFilmBackend(service.docker_supervisor,service.artifacts,builder,verifier,timeout_seconds=600)
                service.openfilm_reviewer=LocalAssetReviewer(service)
                class FixturePlatform:
                    def check(self,run,brief_ref,brief):
                        now=time.time(); source=service.artifacts.put(b'Synthetic fixture policy: requested media dimensions and formats are accepted for this test only; no live platform acceptance is claimed.')
                        assets=[{**{key:item[key] for key in ('channel','kind','width','height','duration_seconds')},'source_ref':source,'supported':True} for item in brief['assets']]
                        return service.artifacts.put(json.dumps({'brief_ref':brief_ref,'checked_at':now,'expires_at':now+3600,'assets':assets}).encode())
                    def verify(self,*args): return True
                if platform_source:
                    from runner.platform_reviewer import PlatformReviewer
                    service.required_skills['platform_review']=['platform-editor']
                    service.openfilm_platform_authority=PlatformReviewer(service,{'sources':{'instagram':{'url':platform_source,'surface':'Instagram API feed image'}}})
                else: service.openfilm_platform_authority=FixturePlatform()
                service.approve('controller','docker_config',runtime.policy_hash,'fixture-operator')
                service.approve('controller','openfilm_builder_policy',builder.policy_hash,'fixture-operator')
                service.approve('controller','openfilm_policy',service.openfilm_backend.policy_hash,'fixture-operator')
            service.page_rules={'note':{'headings':[{'level':1,'text':'Example Domain'}]}}
            service.source_snapshots=SourceSnapshots(service,SourceFetcher([source_url],timeout=15)); service.source_reviewer=SourceReviewer(service)
            if synthetic_source: service.source_verifier=fixture_verifier
            task={'task_id':'fixture','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[],'format':'note',
                  'description':f'Write a short note with the exact Markdown heading # Example Domain. The only claim is that the HTML document title is Example Domain. Use only {source_url} as source; assign source-1, evidence-1 and claim-1. Do not add claims.'}
            if render: task['description']+=' For the separate visuals only, create one 1080 by 1080 PNG for each of LinkedIn, X, Facebook, Instagram, and Reddit, plus one Instagram Reel MP4 at 1080 by 1920 pixels lasting 8 seconds. Give each PNG a visibly distinct treatment described in its alt_text: LinkedIn navy with a cream outline; X charcoal with cyan corner arcs; Facebook sky blue with a cream circle; Instagram coral-to-orange gradient with a small white ring fixed in the upper-right corner and the heading centered in a clear band below it; Reddit coral with a dark rounded shape. Keep the ring away from every letter. Use no platform logos or names. Use the single approved Instagram draft for both Instagram assets; there is no separate Reel draft. Keep the Instagram Reel heading fixed in a clear center band with a gentle fade; move a geometric shape left to right only within the upper 20 percent of the frame. The shape must never touch, cross, or sit behind any letter; sample frames must show visible motion. Do not use external media or add claims. The only visible words on every visual must be the exact approved heading Example Domain; put that concise excerpt first in approved_text and keep the full channel draft there only for traceability. Keep each design simple with generous margins and no overlapping text. This visual restriction does not limit the note body.'
            service.create('r','marketing','fixture'); service.enqueue('r'); queue=service.lifecycle.claim(1200); claim=service.graph.claim('r',1200)
            self.assertTrue(manual_fence_ready(service,'r'))
            task_ref=service.artifacts.put(json.dumps(task).encode())
            service.graph.db.execute('CREATE TABLE task_input_versions(run TEXT PRIMARY KEY,source_version TEXT NOT NULL,artifact_ref TEXT NOT NULL)')
            service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','fixture-v1',task_ref))
            dispatcher=StageDispatcher(service)
            results=[]
            for _ in range(9):
                result=dispatcher.step('r',queue['token'],claim); results.append((result.get('stage'),result.get('status')))
                if result.get('stage')=='freeze' or (result.get('status') in ('blocked','failed','uncertain','rate_limited') and result.get('stage')!='canonical_review'): break
            self.assertEqual(results[:3],[('intake','passed'),('brief','passed'),('canonical_draft','passed')])
            self.assertIn(('canonical_review','passed'),results)
            self.assertEqual(results[-1],('freeze','awaiting_approval'))
            if os.environ.get('WORKFLOW_TEST_CHANNELS')=='1':
                service.approve('r','freeze',result['version'],'fixture-operator')
                self.assertEqual(dispatcher.step('r',queue['token'],claim),{'stage':'freeze','status':'passed'})
                for channel in ('linkedin','x','facebook','instagram','reddit'):
                    outcome=dispatcher.step('r',queue['token'],claim)
                    self.assertEqual((outcome['stage'],outcome['status']),(channel+'_draft','passed'))
                outcome=dispatcher.step('r',queue['token'],claim)
                self.assertEqual((outcome['stage'],outcome['status']),('asset_brief','passed'))
                if render:
                    brief_ref=json.loads(service.artifacts.get(service.graph.get('r','asset_brief')['version']))['outputs'][0]
                    assets=json.loads(service.artifacts.get(brief_ref))['assets']
                    still=next(item for item in assets if item['channel']=='instagram' and item['kind']=='png')['alt_text'].lower()
                    reel=next(item for item in assets if item['channel']=='instagram' and item['kind']=='mp4')['alt_text'].lower()
                    self.assertTrue('ring' in still and 'upper-right' in still and 'away from every letter' in still)
                    self.assertTrue('shape' in reel and 'upper 20%' in reel and 'clear center band' in reel and 'away from every letter' in reel)
                outcome=dispatcher.step('r',queue['token'],claim)
                if platform_source:
                    self.assertEqual((outcome['stage'],outcome['status']),('asset_production','blocked'))
                    record=json.loads(service.artifacts.get(outcome['artifact_ref']))
                    brief_ref=json.loads(service.artifacts.get(service.graph.get('r','asset_brief')['version']))['outputs'][0]
                    brief=json.loads(service.artifacts.get(brief_ref))
                    self.assertTrue(service.openfilm_platform_authority.verify_blocked('r',brief_ref,brief,record['platform_block_ref']))
                    self.assertEqual(service.docker_assignments.db.execute('SELECT count(*) FROM docker_assignments').fetchone()[0],0)
                elif render:
                    self.assertEqual((outcome['stage'],outcome['status']),('asset_production','awaiting_review'))
                    manifest=json.loads(service.artifacts.get(service.graph.get('r','asset_production')['version']))
                    self.assertEqual({(item['channel'],item['kind']) for item in manifest['assets']},{(channel,'png') for channel in ('linkedin','x','facebook','instagram','reddit')}|{('instagram','mp4')})
                    stills=[item for item in manifest['assets'] if item['kind']=='png']
                    self.assertTrue(all(item['width']==1080 and item['height']==1080 for item in stills))
                    self.assertEqual(len({item['sha256'] for item in stills}),5)
                    reel=next(item for item in manifest['assets'] if item['channel']=='instagram' and item['kind']=='mp4')
                    self.assertEqual((reel['width'],reel['height'],reel['duration_seconds']),(1080,1920,8))
                    layout=json.loads(service.artifacts.get(reel['look_report_ref']))
                    self.assertFalse(any(line.startswith('! every frame looks the same') for line in layout['lines']))
                    outcome=dispatcher.step('r',queue['token'],claim)
                    self.assertEqual((outcome['stage'],outcome['status']),('asset_production','passed'))
                    asset_record=json.loads(service.artifacts.get(service.graph.get('r','asset_production')['version']))
                    checks=json.loads(service.artifacts.get(asset_record['report_ref']))['assets']
                    self.assertTrue(all(value is True for item in checks for key,value in item.items() if key!='artifact_ref'))
                else: self.assertEqual((outcome['stage'],outcome['status']),('asset_production','blocked'))
                for channel in ('linkedin','x','facebook','instagram','reddit'):
                    outcome=dispatcher.step('r',queue['token'],claim)
                    self.assertEqual(outcome['stage'],channel+'_review'); self.assertIn(outcome['status'],('passed','blocked'))
                    if channel=='reddit': self.assertEqual(outcome['status'],'blocked')
                    elif render and not platform_source: self.assertEqual(outcome['status'],'passed')
                    if channel=='instagram' and (not render or platform_source): self.assertEqual(outcome['status'],'blocked')
                    self.assertTrue(dispatcher._verify_recorded_review('r',channel+'_review'))
                    draft=json.loads(service.artifacts.get(service.graph.get('r',channel+'_draft')['version']))
                    review=json.loads(service.artifacts.get(service.graph.get('r',channel+'_review')['version']))
                    self.assertNotEqual(draft['session_id'],review['session_id'])
                outcome=dispatcher.step('r',queue['token'],claim)
                self.assertEqual((outcome['stage'],outcome['status']),('assembly','awaiting_review'))
                package=json.loads(service.artifacts.get(service.graph.get('r','assembly')['version']))
                if not render or platform_source:
                    self.assertEqual(package['stages']['asset_production']['status'],'blocked')
                    self.assertEqual(package['stages']['instagram_review']['status'],'blocked')
                if os.environ.get('WORKFLOW_TEST_DECISION')=='1':
                    from types import SimpleNamespace
                    from runner.human_review import HumanReview, HumanReviewError
                    service.task_intake=SimpleNamespace(revalidate=lambda run:run=='r')
                    review=HumanReview(service); version=service.graph.get('r','assembly')['version']
                    self.assertTrue(service.verify_terminal('r'))
                    with self.assertRaises(HumanReviewError): review.decide('r',queue['token'],claim,'0'*64,'approved')
                    service.source_snapshots.fetcher.max_bytes-=1
                    with self.assertRaises(HumanReviewError): review.decide('r',queue['token'],claim,version,'approved')
                    self.assertEqual(service.graph.db.execute('SELECT count(*) FROM human_review_decisions').fetchone()[0],0)
                    service.source_snapshots.fetcher.max_bytes+=1
                    self.assertEqual(review.decide('r',queue['token'],claim,version,'approved')['version'],version)
                    self.assertEqual(review.current('r')['decision'],'approved')
            self.assertFalse(unresolved_scope_rows(service.graph.db,'r')); self.assertFalse(unresolved_scope_rows(service.graph.db,'discovery'))
            service.request_lifecycle_stop('r')
            self.assertFalse(manual_fence_ready(service,'r'))
            completed=True
        finally:
            if service.docker_assignments is not None:
                for (cid,) in service.docker_assignments.db.execute('SELECT container_id FROM docker_assignments WHERE container_id IS NOT NULL').fetchall():
                    if not re.fullmatch(r'[0-9a-f]{64}',cid): raise RuntimeError('fixture container identity unavailable')
                    subprocess.run(['/usr/bin/podman','rm','-f',cid],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=True)
            service.lifecycle.close(); service.close()
            from tests.test_local_executor_crash_live import cleanup_crash_fixture
            if completed and os.environ.get('WORKFLOW_TEST_PRESERVE')!='1': cleanup_crash_fixture(temporary,backend)
            else:
                from types import SimpleNamespace
                cleanup_crash_fixture(SimpleNamespace(name=str(root),cleanup=lambda:None),backend)
                print('Private marketing-graph fixture retained at '+str(root))
