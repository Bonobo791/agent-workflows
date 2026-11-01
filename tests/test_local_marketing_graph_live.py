"""Opt-in signed-in graph dispatch using a public, non-campaign fixture."""
import json
import os
from pathlib import Path
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
            'writer':'Write only the requested note using supplied verified evidence and required heading.',
            'editor':'Review the assigned draft against the supplied source and rules. Report actual material problems only.',
            'source':'Review supplied source snapshots. A page is primary evidence of its own HTML title.',
            'channel-writer':'Adapt the supplied frozen content for the assigned channel. Do not introduce new claims or publish.',
            'channel-editor':'Independently review the assigned channel draft against its supplied input. Report actual material problems only.',
        }.items():
            directory=skills/name; directory.mkdir(mode=0o700); (directory/'SKILL.md').write_text(content)
        service=Service(str(root)); completed=False
        try:
            config=inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],str(workspace),os.environ); backend=SystemdScopes(os.environ)
            discovery=start_local_process(str(root/'state.db'),'discovery',str(workspace),config['codex_executable'],os.environ,backend,lambda:True,config['disabled_integrations'])
            try:
                app=AppServer(discovery.open_transport(time.monotonic()+90,lambda:False)); app.initialize()
                self.assertTrue(discovery.verify_config(app._request)); self.assertIsInstance(app._request('account/read',{'refreshToken':False}).get('account'),dict)
                policy=ModelPolicy(); policy.discover(app._request)
            finally: discovery.finish()
            supervisor=LocalSupervisor(str(root/'state.db'),config['codex_executable'],os.environ,backend,lambda run:True,lambda run,role:str(workspace),lambda:True,config['disabled_integrations'])
            service.model_supervisor=supervisor; service.model_policy=policy
            service.model_executor=CodexExecutor(supervisor,service.provenance,service.artifacts,lambda run:None,model_policy=policy)
            service.skill_resolver=SkillResolver([str(skills)])
            service.required_skills={'brief':['brief'],'canonical_draft':['writer'],'canonical_review':['editor'],'source_review':['source']}
            if os.environ.get('WORKFLOW_TEST_CHANNELS')=='1':
                service.required_skills.update({name+'_draft':['channel-writer'] for name in ('linkedin','x','facebook','instagram','reddit')})
                service.required_skills['linkedin_review']=['channel-editor']
            service.page_rules={'note':{'headings':[{'level':1,'text':'Example Domain'}]}}
            service.source_snapshots=SourceSnapshots(service,SourceFetcher(['https://example.com/'],timeout=15)); service.source_reviewer=SourceReviewer(service)
            task={'task_id':'fixture','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[],'format':'note',
                  'description':'Write a short note with the exact Markdown heading # Example Domain. The only claim is that the HTML document title is Example Domain. Use only https://example.com/ as source; assign claim-1, source-1 and evidence-1. Do not add claims.'}
            service.create('r','marketing','fixture'); service.enqueue('r'); queue=service.queue.claim('fixture',600); claim=service.graph.claim('r',600)
            task_ref=service.artifacts.put(json.dumps(task).encode())
            service.graph.db.execute('CREATE TABLE task_input_versions(run TEXT PRIMARY KEY,source_version TEXT NOT NULL,artifact_ref TEXT NOT NULL)')
            service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','fixture-v1',task_ref))
            dispatcher=StageDispatcher(service)
            results=[]
            for _ in range(5):
                result=dispatcher.step('r',queue['token'],claim); results.append((result.get('stage'),result.get('status')))
                if result.get('stage')=='freeze' or result.get('status') in ('blocked','failed','uncertain','rate_limited'): break
            self.assertEqual(results,[('intake','passed'),('brief','passed'),('canonical_draft','passed'),('canonical_review','passed'),('freeze','awaiting_approval')])
            if os.environ.get('WORKFLOW_TEST_CHANNELS')=='1':
                service.approve('r','freeze',result['version'],'fixture-operator')
                self.assertEqual(dispatcher.step('r',queue['token'],claim),{'stage':'freeze','status':'passed'})
                for channel in ('linkedin','x','facebook','instagram','reddit'):
                    outcome=dispatcher.step('r',queue['token'],claim)
                    self.assertEqual((outcome['stage'],outcome['status']),(channel+'_draft','passed'))
                outcome=dispatcher.step('r',queue['token'],claim)
                self.assertEqual(outcome['stage'],'linkedin_review'); self.assertIn(outcome['status'],('passed','blocked'))
                self.assertTrue(dispatcher._verify_recorded_review('r','linkedin_review'))
                draft=json.loads(service.artifacts.get(service.graph.get('r','linkedin_draft')['version']))
                review=json.loads(service.artifacts.get(service.graph.get('r','linkedin_review')['version']))
                self.assertNotEqual(draft['session_id'],review['session_id'])
            self.assertFalse(unresolved_scope_rows(service.graph.db,'r')); self.assertFalse(unresolved_scope_rows(service.graph.db,'discovery'))
            completed=True
        finally:
            service.close()
            if completed: temporary.cleanup()
            else: print('Private marketing-graph fixture retained at '+str(root))
