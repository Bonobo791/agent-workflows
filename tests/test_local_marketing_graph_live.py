"""Opt-in signed-in graph dispatch using a public, non-campaign fixture."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get('WORKFLOW_TEST_PUBLIC_SOURCE')=='1' and os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1' and os.environ.get('CODEX_TEST_EXECUTABLE'),'signed-in marketing probe not configured')
class LocalMarketingGraphLiveTests(unittest.TestCase):
    def test_fixture_reaches_freeze_approval_gate(self):
        from runner.codex_executor import CodexExecutor
        from runner.local_config_inspect import inspect_config
        from runner.local_process import start_local_process
        from runner.local_scope import SystemdScopes, unresolved_scope_rows
        from runner.local_supervisor import LocalSupervisor
        from runner.model_policy import ModelPolicy
        from runner.protocol import AppServer
        from runner.service import Service
        from runner.skills import SkillResolver
        from runner.source_fetch import SourceFetcher
        from runner.source_reviewer import SourceReviewer
        from runner.source_snapshots import SourceSnapshots
        from runner.stage_dispatch import StageDispatcher

        temporary=tempfile.TemporaryDirectory(prefix='workflow-marketing-graph-live-',delete=False)
        root=Path(temporary.name); workspace=root/'workspace'; workspace.mkdir(mode=0o700)
        skills=root/'skills'; skills.mkdir(mode=0o700)
        for name,content in {
            'brief':'Prepare only the requested fixture brief and exact candidate source register. Do not invent claims.',
            'writer':'Write the requested note using only supplied verified evidence. Include the required heading and both requested supported statements; do not return only a heading.',
            'editor':'Review the assigned draft against the supplied source and rules. Report actual material problems only.',
            'source':'Review supplied source snapshots. A page is primary evidence of its own HTML title.',
            'channel-writer':'Adapt the supplied frozen content for the assigned channel. Do not introduce new claims or publish.',
            'channel-editor':'Independently review the assigned channel draft against its supplied input. Report actual material problems only.',
        }.items():
            directory=skills/name; directory.mkdir(mode=0o700); (directory/'SKILL.md').write_text(content)
        render=os.environ.get('WORKFLOW_TEST_RENDER')=='1'
        if os.environ.get('WORKFLOW_TEST_DECISION')=='1': self.assertEqual(os.environ.get('WORKFLOW_TEST_CHANNELS'),'1','decision probe requires the channel workflow')
        if render:
            self.assertEqual(os.environ.get('WORKFLOW_TEST_CHANNELS'),'1','render probe requires the channel workflow')
            manual=Path(os.environ['OPENFILM_TEST_MANUAL']).read_text()
            directory=skills/'openfilm'; directory.mkdir(mode=0o700); (directory/'SKILL.md').write_text(manual)
            directory=skills/'asset-editor'; directory.mkdir(mode=0o700); (directory/'SKILL.md').write_text('Independently inspect the supplied fixture images and evidence. Do not infer missing evidence.')
        service=Service(str(root)); completed=False
        try:
            config=inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],str(workspace),os.environ); backend=SystemdScopes(os.environ)
            discovery=start_local_process(str(root/'state.db'),'discovery',str(workspace),config['codex_executable'],os.environ,backend,lambda:True,config['disabled_integrations'])
            try:
                app=AppServer(discovery.open_transport(time.monotonic()+90,lambda:False)); app.initialize()
                self.assertTrue(discovery.verify_config(app._request)); self.assertIsInstance(app._request('account/read',{'refreshToken':False}).get('account'),dict)
                policy=ModelPolicy(); policy.discover(app._request)
            finally: discovery.finish()
            if render:
                from runner.local_config import local_supervisor_factory
                supervisor=local_supervisor_factory(config,os.environ,str(root))(service); supervisor.fence=lambda run:True
            else: supervisor=LocalSupervisor(str(root/'state.db'),config['codex_executable'],os.environ,backend,lambda run:True,lambda run,role:str(workspace),lambda:True,config['disabled_integrations'])
            service.model_supervisor=supervisor; service.model_policy=policy
            service.model_executor=CodexExecutor(supervisor,service.provenance,service.artifacts,lambda run:None,model_policy=policy)
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
                service.docker_supervisor=DockerSupervisor(service.docker_assignments,runtime,lambda run:True)
                service.state_db_path=str(root/'state.db'); service.fence_execution_detached=lambda run:None
                service.required_skills.update({'asset_production':['openfilm'],'asset_review':['asset-editor']})
                verifier=PodmanRenderPolicy(runtime,os.environ)
                builder=CodexOpenFilmProjectBuilder(service,verifier,timeout_seconds=300)
                service.openfilm_backend=DockerOpenFilmBackend(service.docker_supervisor,service.artifacts,builder,verifier,timeout_seconds=600)
                service.openfilm_reviewer=LocalAssetReviewer(service)
                class FixturePlatform:
                    def check(self,run,brief_ref,brief):
                        now=time.time(); source=service.artifacts.put(b'Synthetic fixture policy: still image dimensions are accepted for this test only.')
                        assets=[{**{key:item[key] for key in ('channel','kind','width','height','duration_seconds')},'source_ref':source,'supported':True} for item in brief['assets']]
                        return service.artifacts.put(json.dumps({'brief_ref':brief_ref,'checked_at':now,'expires_at':now+3600,'assets':assets}).encode())
                    def verify(self,*args): return True
                service.openfilm_platform_authority=FixturePlatform()
                service.approve('controller','docker_config',runtime.policy_hash,'fixture-operator')
                service.approve('controller','openfilm_builder_policy',builder.policy_hash,'fixture-operator')
                service.approve('controller','openfilm_policy',service.openfilm_backend.policy_hash,'fixture-operator')
            service.page_rules={'note':{'headings':[{'level':1,'text':'Example Domain'}]}}
            service.source_snapshots=SourceSnapshots(service,SourceFetcher(['https://example.com/'],timeout=15)); service.source_reviewer=SourceReviewer(service)
            task={'task_id':'fixture','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[],'format':'note',
                  'description':'Write a short note with the exact Markdown heading # Example Domain. The only claims are that the HTML document title is Example Domain and the domain is for documentation examples. Use only https://example.com/ as source; assign source-1 and evidence-1 and distinct claim IDs. Do not add claims.'}
            if render: task['description']+=' Request one Instagram PNG at 1080 by 1080 pixels using only the exact approved text Example Domain, with no external media.'
            service.create('r','marketing','fixture'); service.enqueue('r'); queue=service.queue.claim('fixture',1200); claim=service.graph.claim('r',1200)
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
                for channel in ('linkedin','x','facebook','reddit'):
                    outcome=dispatcher.step('r',queue['token'],claim)
                    self.assertEqual(outcome['stage'],channel+'_review'); self.assertIn(outcome['status'],('passed','blocked'))
                    if channel=='reddit': self.assertEqual(outcome['status'],'blocked')
                    self.assertTrue(dispatcher._verify_recorded_review('r',channel+'_review'))
                    draft=json.loads(service.artifacts.get(service.graph.get('r',channel+'_draft')['version']))
                    review=json.loads(service.artifacts.get(service.graph.get('r',channel+'_review')['version']))
                    self.assertNotEqual(draft['session_id'],review['session_id'])
                outcome=dispatcher.step('r',queue['token'],claim)
                self.assertEqual(outcome['stage'],'asset_brief'); self.assertIn(outcome['status'],('passed','blocked'))
                outcome=dispatcher.step('r',queue['token'],claim)
                if render:
                    self.assertEqual((outcome['stage'],outcome['status']),('asset_production','awaiting_review'))
                    manifest=json.loads(service.artifacts.get(service.graph.get('r','asset_production')['version']))
                    self.assertTrue(manifest['assets']); self.assertTrue(all(item['kind']=='png' for item in manifest['assets']))
                    outcome=dispatcher.step('r',queue['token'],claim)
                    self.assertEqual(outcome['stage'],'asset_production'); self.assertIn(outcome['status'],('passed','blocked'))
                else: self.assertEqual((outcome['stage'],outcome['status']),('asset_production','blocked'))
                outcome=dispatcher.step('r',queue['token'],claim)
                self.assertEqual(outcome['stage'],'instagram_review'); self.assertIn(outcome['status'],('passed','blocked'))
                if not render: self.assertEqual(outcome['status'],'blocked')
                self.assertTrue(dispatcher._verify_recorded_review('r','instagram_review'))
                outcome=dispatcher.step('r',queue['token'],claim)
                self.assertEqual((outcome['stage'],outcome['status']),('assembly','awaiting_review'))
                package=json.loads(service.artifacts.get(service.graph.get('r','assembly')['version']))
                if not render:
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
            completed=True
        finally:
            if service.docker_assignments is not None:
                for (cid,) in service.docker_assignments.db.execute('SELECT container_id FROM docker_assignments WHERE container_id IS NOT NULL').fetchall():
                    if not re.fullmatch(r'[0-9a-f]{64}',cid): raise RuntimeError('fixture container identity unavailable')
                    subprocess.run(['/usr/bin/podman','rm','-f',cid],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=True)
            service.close()
            if completed: temporary.cleanup()
            else: print('Private marketing-graph fixture retained at '+str(root))
