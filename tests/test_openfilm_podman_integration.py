"""Opt-in real rootless Podman render; uses only synthetic project content."""
import concurrent.futures
from contextlib import closing
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


def _render_fixture():
    from tests.test_openfilm_stage import OpenFilmStageTests
    fixture=OpenFilmStageTests('test_production_ingests_actual_output_but_requires_review')
    directory=tempfile.TemporaryDirectory
    with patch('tests.test_openfilm_stage.tempfile.TemporaryDirectory',side_effect=lambda:directory(delete=False)):
        fixture.setUp()
    return fixture


def _remove_render_containers(assignments,removed):
    """Remove exact recorded IDs once, continuing after individual failures."""
    if not assignments: return
    error=None
    for (cid,) in assignments.db.execute('SELECT container_id FROM docker_assignments WHERE container_id IS NOT NULL'):
        if cid in removed: continue
        try:
            subprocess.run(['podman','rm','-f',cid],check=True,stdout=subprocess.DEVNULL,timeout=30)
            removed.add(cid)
        except (OSError,subprocess.SubprocessError) as failure:
            if error is None: error=failure
    return error


def _drain_render_worker(pool,future):
    """Return whether the submitted worker is quiescent within its cleanup budget."""
    pending={future} if future is not None else set()
    if pool:
        try:
            pending=concurrent.futures.wait([future],timeout=5).not_done if future is not None else set()
        finally: pool.shutdown(wait=not pending,cancel_futures=True)
    return not pending


def _cleanup_render_fixture(fixture,assignments,pool=None,future=None):
    """Fence launches, drain the worker and sweep late IDs before deleting state."""
    supervisor=fixture.service.docker_supervisor
    if supervisor is not None: supervisor.fence_ready=lambda _:False
    removed=set();retain=True
    try:
        error=_remove_render_containers(assignments,removed)
        if not _drain_render_worker(pool,future): raise AssertionError('render worker cleanup unresolved; retained state: '+str(fixture.root))
        if future is not None:
            late_error=_remove_render_containers(assignments,removed)
            if error is None: error=late_error
        if error is not None:
            error.add_note('retained fixture state: '+str(fixture.root))
            raise error
        retain=False
    finally:
        try:
            if assignments: assignments.close()
        finally:
            fixture.service.docker_supervisor=None
            if retain:
                with patch.object(fixture.tmp,'cleanup'): fixture.tearDown()
            else: fixture.tearDown()


class RenderFixtureCleanupTests(unittest.TestCase):
    def _fixture(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        assignments=MagicMock()
        assignments.db.execute.return_value=[('a'*64,),('b'*64,)]
        directory=tempfile.TemporaryDirectory(delete=False);self.addCleanup(directory.cleanup)
        fixture=SimpleNamespace(service=SimpleNamespace(docker_supervisor=SimpleNamespace(fence_ready=lambda _:True)),tmp=directory,root=Path(directory.name))
        fixture.tearDown=MagicMock(side_effect=lambda:fixture.tmp.cleanup())
        return fixture,assignments

    def test_removes_containers_before_waiting_for_worker(self):
        from unittest.mock import MagicMock
        fixture,assignments=self._fixture();future=concurrent.futures.Future();removed=[]
        def remove(argv,**kwargs):
            self.assertEqual(argv[:3],['podman','rm','-f']);removed.append(argv[-1])
            if len(removed)==2: future.set_result(None)
        pool=MagicMock()
        def shutdown(**kwargs):
            self.assertEqual(removed,['a'*64,'b'*64]);self.assertTrue(future.done())
            self.assertEqual(kwargs,{'wait':True,'cancel_futures':True})
        pool.shutdown.side_effect=shutdown
        with patch(__name__+'.subprocess.run',side_effect=remove):
            _cleanup_render_fixture(fixture,assignments,pool,future)
        assignments.close.assert_called_once();fixture.tearDown.assert_called_once()
        self.assertIsNone(fixture.service.docker_supervisor)

    def test_removal_failure_still_attempts_remaining_containers_and_closes_fixture(self):
        fixture,assignments=self._fixture();error=subprocess.TimeoutExpired('podman rm',30)
        with patch(__name__+'.subprocess.run',side_effect=[error,None]) as remove:
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                _cleanup_render_fixture(fixture,assignments)
        self.assertIs(raised.exception,error);self.assertEqual(remove.call_count,2)
        assignments.close.assert_called_once();fixture.tearDown.assert_called_once()
        self.assertIsNone(fixture.service.docker_supervisor)

    def test_worker_that_does_not_exit_is_reported_without_blocking_shutdown(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        fixture,assignments=self._fixture();pool=MagicMock();future=concurrent.futures.Future()
        pending=SimpleNamespace(not_done={future})
        with patch(__name__+'.subprocess.run'),patch(__name__+'.concurrent.futures.wait',return_value=pending) as wait:
            with self.assertRaisesRegex(AssertionError,'render worker cleanup unresolved'):
                _cleanup_render_fixture(fixture,assignments,pool,future)
        wait.assert_called_once_with([future],timeout=5)
        pool.shutdown.assert_called_once_with(wait=False,cancel_futures=True)
        assignments.close.assert_called_once();fixture.tearDown.assert_called_once()
        self.assertTrue(fixture.root.exists(),'pending worker evidence was deleted')

    def test_delayed_create_is_fenced_and_swept_after_worker_exits(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        fixture,assignments=self._fixture();supervisor=fixture.service.docker_supervisor
        rows=[];removed=[];future=concurrent.futures.Future();pool=MagicMock()
        assignments.db.execute.side_effect=lambda *_:list(rows)
        def finish_create(*args,**kwargs):
            self.assertFalse(supervisor.fence_ready('run'))
            assignments.close.assert_not_called()
            rows.append(('c'*64,));future.set_result(None)
            return SimpleNamespace(not_done=set())
        with patch(__name__+'.subprocess.run',side_effect=lambda argv,**_:removed.append(argv[-1])),patch(__name__+'.concurrent.futures.wait',side_effect=finish_create):
            _cleanup_render_fixture(fixture,assignments,pool,future)
        self.assertEqual(removed,['c'*64]);assignments.close.assert_called_once()
        self.assertFalse(fixture.root.exists())


    def test_pending_worker_keeps_real_state_database_for_late_response(self):
        import sqlite3
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from runner.docker_controller import DockerAssignments
        fixture=_render_fixture();self.addCleanup(fixture.tmp.cleanup);self.addCleanup(fixture.service.close);self.addCleanup(fixture.patch.stop)
        path=str(fixture.root/'state'/'state.db');assignments=DockerAssignments(path,None,'sha256:'+'1'*64)
        assignments.db.execute("INSERT INTO docker_assignments VALUES('late',NULL,'creating','fixture')")
        pending=concurrent.futures.Future();pool=MagicMock()
        with patch(__name__+'.concurrent.futures.wait',return_value=SimpleNamespace(not_done={pending})):
            with self.assertRaisesRegex(AssertionError,'retained state:'):
                _cleanup_render_fixture(fixture,assignments,pool,pending)
        self.assertTrue(Path(path).is_file())
        db=sqlite3.connect(path)
        try:
            db.execute("UPDATE docker_assignments SET container_id=?,state='created' WHERE run='late'",('c'*64,));db.commit()
            self.assertEqual(db.execute("SELECT container_id FROM docker_assignments WHERE run='late'").fetchone(),('c'*64,))
        finally: db.close()
        pool.shutdown.assert_called_once_with(wait=False,cancel_futures=True)


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

    def _render_backend(self,fixture,engine,*,slow=False):
        from runner.docker_config import DockerConfig
        from runner.docker_controller import DockerAssignments
        from runner.docker_supervisor import DockerSupervisor
        from runner.docker_openfilm import DockerOpenFilmBackend
        jobs=fixture.root/'state'/'jobs'; jobs.mkdir(mode=0o700)
        config=DockerConfig({'socket':engine.path,'image':os.environ['OPENFILM_PODMAN_IMAGE'],'network':'none','job_root':str(jobs),'engine':'podman-rootless'},fixture.root/'state')
        archive=io.BytesIO(); source=Path(__file__).parent/'fixtures'/'openfilm'
        with zipfile.ZipFile(archive,'w') as z:
            for file in source.iterdir():
                if slow and file.name=='scene.html':
                    scene=file.read_text()
                    self.assertIn('frame(t){}',scene)
                    scene=scene.replace('frame(t){}','frame(t){const end=performance.now()+30000; while(performance.now()<end){}}')
                    z.writestr(file.name,scene)
                else: z.write(file,file.name)
        project=fixture.service.artifacts.put(archive.getvalue()); proof=fixture.terminal
        class Builder:
            def ready(self): return True
            def supports(self,brief): return True
            def build(self,**kwargs): return {'project_ref':project,'proof_ref':proof}
            def verify(self,run,attempt_id,brief_ref,project_ref,proof_ref): return project_ref==project and proof_ref==proof
            def cancel(self,*args): pass
        assignments=DockerAssignments(str(fixture.root/'state'/'state.db'),engine,config.image)
        try:
            supervisor=DockerSupervisor(assignments,config,lambda run:True); fixture.service.docker_supervisor=supervisor
            backend=DockerOpenFilmBackend(supervisor,fixture.service.artifacts,Builder(),lambda policy:True,timeout_seconds=120)
            fixture.service.openfilm_backend=backend
            fixture.service.approve('controller','docker_config',config.policy_hash,'synthetic')
            fixture.service.approve('controller','openfilm_policy',backend.policy_hash,'synthetic')
            return assignments,supervisor,backend
        except BaseException:
            assignments.close(); fixture.service.docker_supervisor=None
            raise

    def test_cancellation_stops_real_render_without_committing_result(self):
        import sqlite3
        import threading
        from runner.docker_controller import DockerError, UnixEngine
        fixture=_render_fixture()
        assignments=None; pool=None; future=None; attempt=None
        try:
            started=threading.Event(); raw=UnixEngine(os.environ['OPENFILM_PODMAN_SOCKET'])
            class Engine:
                path=raw.path
                def request(self,method,path,body=None,*,timeout=None):
                    if path.endswith('/wait?condition=not-running') and timeout is not None: started.set()
                    return raw.request(method,path,body,timeout=timeout)
            engine=Engine()
            assignments,supervisor,backend=self._render_backend(fixture,engine,slow=True)
            count=fixture.service.graph.begin_attempt('run','asset_production','cancel-probe',fixture.claim,2)
            attempt=hashlib.sha256(json.dumps(['run','asset_production','cancel-probe',count],separators=(',',':')).encode()).hexdigest()
            pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future=pool.submit(backend.execute,run='run',attempt_id=attempt,brief_ref=fixture.brief_ref,brief=fixture.brief,canonical='Approved synthetic statement.',graph_claim=fixture.claim,queue_claim=fixture.queue)
            self.assertTrue(started.wait(20),'render did not reach the Engine wait')
            with closing(sqlite3.connect(supervisor.db_path)) as db:
                row=db.execute('SELECT container_id,state FROM docker_assignments WHERE run=?',('of-'+attempt,)).fetchone()
            self.assertIsNotNone(row,'render assignment unavailable')
            cid,state=row
            self.assertEqual(state,'running')
            self.assertTrue(raw.request('GET','/containers/'+cid+'/json')[1]['State']['Running'])
            backend.cancel('run',attempt)
            with self.assertRaises(DockerError): future.result(timeout=30)
            with closing(sqlite3.connect(supervisor.db_path)) as db:
                self.assertEqual(db.execute('SELECT state,result_ref FROM openfilm_render_jobs WHERE assignment=?',('of-'+attempt,)).fetchone(),('cancel_requested',None))
                self.assertEqual(db.execute('SELECT state FROM docker_assignments WHERE run=?',('of-'+attempt,)).fetchone(),('terminated',))
                self.assertEqual(db.execute("SELECT status FROM graph_attempts WHERE run='run' AND stage='asset_production'").fetchone(),('uncertain',))
            status,details=raw.request('GET','/containers/'+cid+'/json')
            self.assertEqual(status,200)
            self.assertFalse(details['State']['Running'])
        finally:
            _cleanup_render_fixture(fixture,assignments,pool,future)

    def test_cleanup_removes_container_created_after_initial_sweep(self):
        import threading
        from runner.docker_controller import DockerError, UnixEngine
        fixture=_render_fixture();assignments=None;pool=None;future=None;cleaning=False
        created=threading.Event();release=threading.Event();containers=[];starts=[]
        raw=UnixEngine(os.environ['OPENFILM_PODMAN_SOCKET'])
        class Engine:
            path=raw.path
            def request(self,method,path,body=None,*,timeout=None):
                if path.endswith('/start'): starts.append(path)
                result=raw.request(method,path,body,timeout=timeout)
                if method=='POST' and path=='/containers/create':
                    containers.append(result[1]['Id']);created.set()
                    if not release.wait(10): raise DockerError('delayed create response was not released')
                return result
        try:
            assignments,supervisor,backend=self._render_backend(fixture,Engine())
            count=fixture.service.graph.begin_attempt('run','asset_production','late-create',fixture.claim,2)
            attempt=hashlib.sha256(json.dumps(['run','asset_production','late-create',count],separators=(',',':')).encode()).hexdigest()
            pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future=pool.submit(backend.execute,run='run',attempt_id=attempt,brief_ref=fixture.brief_ref,brief=fixture.brief,canonical='Approved synthetic statement.',graph_claim=fixture.claim,queue_claim=fixture.queue)
            self.assertTrue(created.wait(20),'Engine did not create the fixture container')
            self.assertEqual(assignments.db.execute('SELECT container_id,state FROM docker_assignments WHERE run=?',('of-'+attempt,)).fetchone(),(None,'creating'))
            drain=_drain_render_worker
            def resume_response(pool,future):
                self.assertFalse(supervisor.fence_ready('run'))
                release.set()
                return drain(pool,future)
            cleaning=True
            with patch(__name__+'._drain_render_worker',side_effect=resume_response):
                _cleanup_render_fixture(fixture,assignments,pool,future)
            self.assertIsInstance(future.exception(),DockerError)
            self.assertEqual(starts,[])
            self.assertEqual(raw.request('GET','/containers/'+containers[0]+'/json')[0],404)
            self.assertFalse(fixture.root.exists())
        finally:
            release.set()
            if not cleaning: _cleanup_render_fixture(fixture,assignments,pool,future)

    def test_render_and_independent_terminal_verification(self):
        from runner.docker_controller import UnixEngine
        fixture=_render_fixture()
        assignments=None
        try:
            engine=UnixEngine(os.environ['OPENFILM_PODMAN_SOCKET'])
            assignments,supervisor,backend=self._render_backend(fixture,engine)
            count=fixture.service.graph.begin_attempt('run','asset_production','probe',fixture.claim,2)
            attempt=hashlib.sha256(json.dumps(['run','asset_production','probe',count],separators=(',',':')).encode()).hexdigest()
            result=backend.execute(run='run',attempt_id=attempt,brief_ref=fixture.brief_ref,brief=fixture.brief,canonical='Approved synthetic statement.',graph_claim=fixture.claim,queue_claim=fixture.queue)
            self.assertEqual(result['status'],'exited')
            self.assertTrue(backend.verify_terminal('run',attempt,fixture.brief_ref,result))
            with patch('runner.docker_openfilm.os.getxattr',return_value=b'wrong-label'):
                self.assertFalse(backend.verify_terminal('run',attempt,fixture.brief_ref,result))
            self.assertEqual(len(result['outputs']),1)
        finally:
            _cleanup_render_fixture(fixture,assignments)
