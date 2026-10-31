"""Opt-in real rootless Podman render; uses only synthetic project content."""
import hashlib
import io
import json
import os
import subprocess
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('OPENFILM_PODMAN_IMAGE') and os.environ.get('OPENFILM_PODMAN_SOCKET'), 'rootless Podman render not configured')
class PodmanRenderIntegrationTests(unittest.TestCase):
    def test_renderer_refuses_remaining_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); workspace=root/'workspace'; output=root/'output'
            workspace.mkdir(); output.mkdir()
            shutil.copytree(Path(__file__).parent/'fixtures'/'openfilm',workspace/'project')
            (workspace/'brief.json').write_text(json.dumps({'assets':[{'kind':'png'}]}))
            command=['podman','run','--rm','--network','none','--read-only','--cap-drop','all','--cap-add','CHOWN',
                     '--security-opt','no-new-privileges','--userns','keep-id','--user',f'{os.geteuid()}:{os.getegid()}',
                     '--tmpfs','/tmp:rw,nosuid,nodev','-e','OPENFILM_HOME=/tmp/openfilm',
                     '-v',str(workspace)+':/workspace:rw,Z','-v',str(output)+':/output:rw,Z',os.environ['OPENFILM_PODMAN_IMAGE']]
            result=subprocess.run(command,capture_output=True,timeout=30)
            self.assertNotEqual(result.returncode,0)
            self.assertIn(b'OpenFilm render or verification failed',result.stderr)
            self.assertFalse(list(output.iterdir()))

    def test_render_and_independent_terminal_verification(self):
        from tests.test_openfilm_stage import OpenFilmStageTests
        from runner.docker_config import DockerConfig
        from runner.docker_controller import DockerAssignments, UnixEngine
        from runner.docker_supervisor import DockerSupervisor
        from runner.docker_openfilm import DockerOpenFilmBackend
        fixture=OpenFilmStageTests('test_production_ingests_actual_output_but_requires_review')
        fixture.setUp(); assignments=None
        try:
            jobs=fixture.root/'state'/'jobs'; jobs.mkdir(mode=0o700)
            engine=UnixEngine(os.environ['OPENFILM_PODMAN_SOCKET'],timeout=120)
            config=DockerConfig({'socket':engine.path,'image':os.environ['OPENFILM_PODMAN_IMAGE'],'network':'none','job_root':str(jobs),'engine':'podman-rootless'},fixture.root/'state')
            archive=io.BytesIO(); source=Path(__file__).parent/'fixtures'/'openfilm'
            with zipfile.ZipFile(archive,'w') as z:
                for file in source.iterdir(): z.write(file,file.name)
            project=fixture.service.artifacts.put(archive.getvalue()); proof=fixture.terminal
            class Builder:
                def ready(self): return True
                def supports(self,brief): return True
                def build(self,**kwargs): return {'project_ref':project,'proof_ref':proof}
                def verify(self,run,attempt_id,brief_ref,project_ref,proof_ref): return project_ref==project and proof_ref==proof
                def cancel(self,*args): pass
            assignments=DockerAssignments(str(fixture.root/'state'/'state.db'),engine,config.image)
            supervisor=DockerSupervisor(assignments,config,lambda run:True); fixture.service.docker_supervisor=supervisor
            backend=DockerOpenFilmBackend(supervisor,fixture.service.artifacts,Builder(),lambda policy:True,timeout_seconds=120)
            fixture.service.openfilm_backend=backend
            fixture.service.approve('controller','docker_config',config.policy_hash,'synthetic')
            fixture.service.approve('controller','openfilm_policy',backend.policy_hash,'synthetic')
            count=fixture.service.graph.begin_attempt('run','asset_production','probe',fixture.claim,2)
            attempt=hashlib.sha256(json.dumps(['run','asset_production','probe',count],separators=(',',':')).encode()).hexdigest()
            result=backend.execute(run='run',attempt_id=attempt,brief_ref=fixture.brief_ref,brief=fixture.brief,canonical='Approved synthetic statement.',graph_claim=fixture.claim,queue_claim=fixture.queue)
            self.assertEqual(result['status'],'exited')
            self.assertTrue(backend.verify_terminal('run',attempt,fixture.brief_ref,result))
            with patch('runner.docker_openfilm.os.getxattr',return_value=b'wrong-label'):
                self.assertFalse(backend.verify_terminal('run',attempt,fixture.brief_ref,result))
            self.assertEqual(len(result['outputs']),1)
        finally:
            if assignments:
                for (cid,) in assignments.db.execute('SELECT container_id FROM docker_assignments WHERE container_id IS NOT NULL'):
                    subprocess.run(['podman','rm','-f',cid],check=True,stdout=subprocess.DEVNULL,timeout=30)
                assignments.close(); fixture.service.docker_supervisor=None
            fixture.tearDown()
