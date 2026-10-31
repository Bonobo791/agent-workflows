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
    def test_credential_free_still_and_vertical_video(self):
        from runner.artifacts import ArtifactStore
        from workflows.marketing.assets import ingest_verified_asset, ingest_verified_preview
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);workspace=root/'workspace';output=root/'output';storage=root/'storage'
            for path in (workspace,output,storage):path.mkdir(mode=0o700)
            fixture=Path(__file__).parent/'fixtures'/'openfilm'
            for index in range(2):shutil.copytree(fixture,workspace/'project'/'projects'/str(index))
            (workspace/'brief.json').write_text(json.dumps({'assets':[{'kind':'png'},{'kind':'mp4'}]}))
            command=['podman','run','--rm','--network','none','--read-only','--cap-drop','all',
                     '--security-opt','no-new-privileges','--userns','keep-id','--user',f'{os.geteuid()}:{os.getegid()}',
                     '--tmpfs','/tmp:rw,nosuid,nodev','-e','OPENFILM_HOME=/tmp/openfilm',
                     '-v',str(workspace)+':/workspace:rw,Z','-v',str(output)+':/output:rw,Z',os.environ['OPENFILM_PODMAN_IMAGE']]
            completed=subprocess.run(command,capture_output=True,timeout=120)
            self.assertEqual(completed.returncode,0,completed.stderr.decode(errors='replace'))
            items=json.loads((output/'render-result.json').read_text())['outputs'];self.assertEqual(len(items),2)
            store=ArtifactStore(str(storage))
            for kind,item in zip(('png','mp4'),items):
                brief={'version':'brief','kind':kind,'width':180,'height':320,'duration_seconds':1}
                binding={'brief_version':'brief','project_version':'project','asset_sha256':item['sha256']}
                self.assertEqual(ingest_verified_asset(store,output,item['path'],brief,binding,'project')['status'],'awaiting_review')
                preview=item['previews'][0]
                self.assertIn('artifact_ref',ingest_verified_preview(store,output,preview['path'],preview['sha256'],'brief','project'))
                report=json.loads((output/item['report']['path']).read_text())
                self.assertTrue(report['ok']);self.assertTrue(any(line.startswith('✓ ') and 'same picture' in line for line in report['lines']))
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

    def test_cancellation_stops_real_render_without_committing_result(self):
        import concurrent.futures
        import sqlite3
        import threading
        from tests.test_openfilm_stage import OpenFilmStageTests
        from runner.docker_config import DockerConfig
        from runner.docker_controller import DockerAssignments, DockerError, UnixEngine
        from runner.docker_supervisor import DockerSupervisor
        from runner.docker_openfilm import DockerOpenFilmBackend
        fixture=OpenFilmStageTests('test_production_ingests_actual_output_but_requires_review')
        fixture.setUp(); assignments=None; pool=None
        try:
            jobs=fixture.root/'state'/'jobs'; jobs.mkdir(mode=0o700)
            started=threading.Event(); raw=UnixEngine(os.environ['OPENFILM_PODMAN_SOCKET'])
            class Engine:
                path=raw.path
                def request(self,method,path,body=None,*,timeout=None):
                    if path.endswith('/wait?condition=not-running') and timeout is not None: started.set()
                    return raw.request(method,path,body,timeout=timeout)
            engine=Engine()
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
            supervisor=DockerSupervisor(assignments,config,lambda run:True);fixture.service.docker_supervisor=supervisor
            backend=DockerOpenFilmBackend(supervisor,fixture.service.artifacts,Builder(),lambda policy:True,timeout_seconds=120)
            fixture.service.openfilm_backend=backend
            fixture.service.approve('controller','docker_config',config.policy_hash,'synthetic')
            fixture.service.approve('controller','openfilm_policy',backend.policy_hash,'synthetic')
            count=fixture.service.graph.begin_attempt('run','asset_production','cancel-probe',fixture.claim,2)
            attempt=hashlib.sha256(json.dumps(['run','asset_production','cancel-probe',count],separators=(',',':')).encode()).hexdigest()
            pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future=pool.submit(backend.execute,run='run',attempt_id=attempt,brief_ref=fixture.brief_ref,brief=fixture.brief,canonical='Approved synthetic statement.',graph_claim=fixture.claim,queue_claim=fixture.queue)
            self.assertTrue(started.wait(20),'render did not reach the Engine wait')
            with sqlite3.connect(supervisor.db_path) as db:
                cid,state=db.execute('SELECT container_id,state FROM docker_assignments WHERE run=?',('of-'+attempt,)).fetchone()
            self.assertEqual(state,'running')
            self.assertTrue(raw.request('GET','/containers/'+cid+'/json')[1]['State']['Running'])
            backend.cancel('run',attempt)
            with self.assertRaises(DockerError): future.result(timeout=30)
            with sqlite3.connect(supervisor.db_path) as db:
                self.assertEqual(db.execute('SELECT state,result_ref FROM openfilm_render_jobs WHERE assignment=?',('of-'+attempt,)).fetchone(),('cancel_requested',None))
                self.assertEqual(db.execute('SELECT state FROM docker_assignments WHERE run=?',('of-'+attempt,)).fetchone(),('terminated',))
                self.assertEqual(db.execute("SELECT status FROM graph_attempts WHERE run='run' AND stage='asset_production'").fetchone(),('uncertain',))
            self.assertFalse(raw.request('GET','/containers/'+cid+'/json')[1]['State']['Running'])
        finally:
            if pool: pool.shutdown(wait=True)
            if assignments:
                for (cid,) in assignments.db.execute('SELECT container_id FROM docker_assignments WHERE container_id IS NOT NULL'):
                    subprocess.run(['podman','rm','-f',cid],check=True,stdout=subprocess.DEVNULL,timeout=30)
                assignments.close();fixture.service.docker_supervisor=None
            fixture.tearDown()

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
            engine=UnixEngine(os.environ['OPENFILM_PODMAN_SOCKET'])
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
