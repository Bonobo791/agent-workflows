import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from runner.local_scope import LocalScopes
from runner.service import Service
from runner.video_samples import VideoSamples,VideoSampleError
from tests.test_assets import png
from tests.test_local_scope import Backend

class VideoSampleTests(unittest.TestCase):
    def test_sample_binds_exact_video_and_terminated_scope(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root);service.state_db_path=str(Path(root)/'state.db');service.create('run','marketing','task');claim=service.claim('run')
            backend=Backend();service.model_supervisor=SimpleNamespace(db=service.state_db_path,backend=backend,policy_hash='a'*64,preflight=lambda:True,fence=lambda run:True,verify_policy=lambda:True)
            try:
                video=service.artifacts.put(b'synthetic mp4');subject=service.artifacts.put(json.dumps({'assets':[{'kind':'mp4','artifact_ref':video,'duration_seconds':1.0}]}).encode())
                with patch.object(VideoSamples,'_ffmpeg_hash',return_value='b'*64):
                    sampler=VideoSamples(service)
                    def execute(supervisor,run,workspace,argv,deadline,output_limit,record):
                        scopes=LocalScopes(service.state_db_path,backend);unit=scopes.reserve(run);record(unit);backend.unit=unit;backend.state='active';scopes.bind(run,unit)
                        Path(argv[-1]).write_bytes(png(1290,968));scopes.stop(run,unit);scopes.close()
                        self.assertIn('file,pipe',argv);self.assertEqual(Path(argv[argv.index('-i')+1]).read_bytes(),b'synthetic mp4')
                        stderr='\n'.join('[Parsed_showinfo_1 @ 0x1] n: %d pts: %d pts_time:%.6f'%(i,i,i/12) for i in range(12)).encode()
                        return {'outcome':'exited','exit_code':0,'unit':unit,'stderr':stderr}
                    with patch('runner.video_samples.run_scoped_sandbox',side_effect=execute):receipt=sampler.sample('run',claim,subject,video)
                    self.assertTrue(sampler.verify('run',subject,video,receipt))
                    self.assertFalse(sampler.verify('other',subject,video,receipt))
                    def missing_frame(*args,**kwargs):
                        result=execute(*args,**kwargs);result['stderr']=b'\n'.join(result['stderr'].splitlines()[:-1]);return result
                    with patch('runner.video_samples.run_scoped_sandbox',side_effect=missing_frame):
                        with self.assertRaises(VideoSampleError):sampler.sample('run',claim,subject,video)
                    def changed_input(*args,**kwargs):
                        result=execute(*args,**kwargs);source=Path(args[3][args[3].index('-i')+1]);source.chmod(0o600);source.write_bytes(b'x'*len(b'synthetic mp4'));return result
                    with patch('runner.video_samples.run_scoped_sandbox',side_effect=changed_input):
                        with self.assertRaises(VideoSampleError):sampler.sample('run',claim,subject,video)
                    bad=service.artifacts.put(json.dumps({'assets':[{'kind':'mp4','artifact_ref':video,'duration_seconds':0}]}).encode())
                    with self.assertRaises(VideoSampleError):sampler.sample('run',claim,bad,video)
                    service.graph.db.execute("UPDATE local_scopes SET state='running' WHERE unit=(SELECT unit FROM local_video_samples WHERE receipt_ref=?)",(receipt,))
                    self.assertFalse(sampler.verify('run',subject,video,receipt))
            finally:service.close()
