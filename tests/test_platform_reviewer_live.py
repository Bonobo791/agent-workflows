"""Opt-in positive control against a public, surface-specific platform source."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get('WORKFLOW_TEST_REAL_PLATFORM')=='1' and os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1' and os.environ.get('CODEX_TEST_EXECUTABLE'),'signed-in platform probe not configured')
class PlatformReviewerLiveTests(unittest.TestCase):
    def test_linkedin_profile_image_against_official_source(self):
        from runner.local_config import local_supervisor_factory
        from runner.local_config_inspect import inspect_config
        from runner.local_process import start_local_process
        from runner.local_scope import SystemdScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.model_policy import ModelPolicy
        from runner.openfilm_platform import verify_platform
        from runner.platform_reviewer import PlatformReviewer
        from runner.protocol import AppServer
        from runner.service import Service
        from runner.skills import SkillResolver
        from runner.worker import manual_fence_ready
        from tests.test_local_executor_crash_live import cleanup_crash_fixture

        temporary=tempfile.TemporaryDirectory(prefix='workflow-platform-live-',delete=False)
        root=Path(temporary.name); workspace=root/'workspace'; workspace.mkdir(mode=0o700)
        skill=root/'skills'/'platform'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('Compare the exact asset and surface to the supplied official source. Do not infer missing requirements.')
        backend=SystemdScopes(os.environ); service=Service(root); service.state_db_path=str(root/'state.db')
        service.lifecycle=ManualLifecycle(service.state_db_path,'fixture','fixture-incarnation'); completed=False
        try:
            config=inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],str(workspace),os.environ)
            discovery=start_local_process(service.state_db_path,'discovery',str(workspace),config['codex_executable'],os.environ,backend,lambda:True,config['disabled_integrations'])
            try:
                app=AppServer(discovery.open_transport(time.monotonic()+90,lambda:False)); app.initialize()
                self.assertTrue(discovery.verify_config(app._request)); self.assertIsInstance(app._request('account/read',{'refreshToken':False}).get('account'),dict)
                service.model_policy=ModelPolicy(); service.model_policy.discover(app._request)
            finally: discovery.finish()
            supervisor=local_supervisor_factory(config,os.environ,str(root))(service)
            supervisor.fence=lambda run:manual_fence_ready(service,run)
            service.model_supervisor=supervisor; service.skill_resolver=SkillResolver([str(root/'skills')]); service.required_skills={'platform_review':['platform']}
            service.create('r','marketing','fixture'); service.enqueue('r'); queue=service.lifecycle.claim(300); claim=service.graph.claim('r',300)
            brief={'assets':[{'channel':'linkedin','kind':'png','width':400,'height':400,'duration_seconds':None}]}
            brief_ref=service.artifacts.put(json.dumps(brief).encode())
            url='https://business.linkedin.com/sell/resources/effective-sales-profile'
            authority=PlatformReviewer(service,{'sources':{'linkedin':{'url':url,'surface':'personal profile photo'}}})
            service.openfilm_platform_authority=authority
            proof=authority.check_for_claim('r',brief_ref,brief,claim,queue['token'])
            self.assertIsInstance(proof,str,'official source did not establish this exact fixture')
            self.assertTrue(verify_platform(service,'r',brief_ref,brief,proof,30,lambda:False))
            changed={'assets':[{**brief['assets'][0],'width':401}]}
            self.assertFalse(authority.verify('r',brief_ref,changed,proof))
            authority.config['sources']['linkedin']['surface']='company logo'
            self.assertFalse(authority.verify('r',brief_ref,brief,proof))
            authority.config['sources']['linkedin']['surface']='personal profile photo'
            self.assertTrue(authority.verify('r',brief_ref,brief,proof))
            service.request_lifecycle_stop('r'); self.assertFalse(manual_fence_ready(service,'r'))
            completed=True
        finally:
            service.lifecycle.close(); service.close()
            if completed: cleanup_crash_fixture(temporary,backend)
            else:
                from types import SimpleNamespace
                cleanup_crash_fixture(SimpleNamespace(name=str(root),cleanup=lambda:None),backend)
                print('Private platform fixture retained at '+str(root))
