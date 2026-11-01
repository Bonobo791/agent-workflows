"""Opt-in signed-in source review of one public, non-campaign fixture."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get('WORKFLOW_TEST_PUBLIC_SOURCE')=='1' and os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1' and os.environ.get('CODEX_TEST_EXECUTABLE'),'signed-in public source probe not configured')
class SourceReviewerLiveTests(unittest.TestCase):
    def test_signed_in_review_binds_fetched_source_and_policy(self):
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
        from runner.source_register import SourceError
        from runner.source_reviewer import SourceReviewer, validate_evidence
        from runner.source_snapshots import SourceSnapshots

        temporary=tempfile.TemporaryDirectory(prefix='workflow-source-review-live-',delete=False)
        root=Path(temporary.name); workspace=root/'workspace'; workspace.mkdir(mode=0o700)
        skill=root/'skills'/'source-review'; skill.mkdir(parents=True,mode=0o700)
        (skill/'SKILL.md').write_text('Review only the supplied source. A page is primary evidence of its own HTML title. Follow the requested output contract exactly.')
        service=Service(str(root)); completed=False
        try:
            config=inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],str(workspace),os.environ)
            backend=SystemdScopes(os.environ)
            discovery=start_local_process(str(root/'state.db'),'discovery',str(workspace),config['codex_executable'],os.environ,backend,lambda:True,config['disabled_integrations'])
            try:
                app=AppServer(discovery.open_transport(time.monotonic()+90,lambda:False)); app.initialize()
                self.assertTrue(discovery.verify_config(app._request))
                self.assertIsInstance(app._request('account/read',{'refreshToken':False}).get('account'),dict)
                model=ModelPolicy().discover(app._request)
            finally: discovery.finish()
            supervisor=LocalSupervisor(str(root/'state.db'),config['codex_executable'],os.environ,backend,lambda run:True,lambda run,role:str(workspace),lambda:True,config['disabled_integrations'])
            service.model_executor=CodexExecutor(supervisor,service.provenance,service.artifacts,lambda run:None)
            service.skill_resolver=SkillResolver([str(skill.parent)]); service.required_skills={'source_review':['source-review']}
            service.source_snapshots=SourceSnapshots(service,SourceFetcher(['https://example.com/'],timeout=15))
            service.create('r','marketing','synthetic'); claim=service.graph.claim('r',180)
            register={'claims':[{'id':'claim-1','claim':'The HTML document title is Example Domain.','source_ids':['source-1']}],
                      'sources':[{'id':'source-1','url':'https://example.com/','evidence_id':'evidence-1'}]}
            register_ref=service.artifacts.put(json.dumps(register).encode())
            evidence=SourceReviewer(service).verify('r',claim,register_ref,model=model,cancelled=lambda:False)
            self.assertTrue(validate_evidence(service,'r',register_ref,evidence))
            service.source_snapshots.fetcher.max_bytes-=1
            with self.assertRaises(SourceError): validate_evidence(service,'r',register_ref,evidence)
            self.assertFalse(unresolved_scope_rows(service.graph.db,'r'))
            self.assertFalse(unresolved_scope_rows(service.graph.db,'discovery'))
            completed=True
        finally:
            service.close()
            if completed: temporary.cleanup()
            else: print('Private source-review fixture retained at '+str(root))
