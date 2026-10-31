import hashlib
import io
import json
import unittest
import wave
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runner.audio_transcriber import AudioTranscriber, AudioTranscriptionError
from runner.local_scope import LocalScopes
from runner.service import Service
from tests.test_local_scope import Backend


class AudioReportTests(unittest.TestCase):
    def test_report_binds_transcript_to_actual_pcm(self):
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes((500).to_bytes(2, 'little', signed=True) * 16000)
        audio = output.getvalue()
        report = {'language': 'pt', 'audio_sha256': hashlib.sha256(audio).hexdigest(),
                  'duration': 1, 'peak': 500, 'rms': 500,
                  'segments': [{'start': 0, 'end': 0.8, 'text': 'teste',
                                'avg_logprob': -0.1, 'no_speech_prob': 0.1}]}
        encode = lambda: json.dumps(report).encode()
        self.assertEqual(AudioTranscriber._report(encode(), audio, 'pt', 1), report)
        report['peak'] = 499
        with self.assertRaises(ValueError):
            AudioTranscriber._report(encode(), audio, 'pt', 1)
        report['peak'] = 500
        report['segments'][0]['end'] = float('nan')
        with self.assertRaises(ValueError):
            AudioTranscriber._report(encode(), audio, 'pt', 1)

    def test_transcript_requires_approval_and_terminated_bound_scope(self):
        with tempfile.TemporaryDirectory() as root:
            service = Service(root)
            service.state_db_path = str(Path(root) / 'state.db')
            service.create('run', 'marketing', 'task')
            claim = service.claim('run')
            backend = Backend()
            service.model_supervisor = SimpleNamespace(db=service.state_db_path, backend=backend,
                policy_hash='a'*64, preflight=lambda: True, fence=lambda run: True, verify_policy=lambda: True)
            try:
                roots = [Path(root)/name for name in ('runtime', 'python', 'model')]
                for path in roots: path.mkdir()
                (roots[0]/'bin').mkdir()
                (roots[1]/'python').write_text('fixture')
                (roots[0]/'bin'/'python-sandbox').symlink_to(roots[1]/'python')
                config = dict(zip(('runtime_root', 'python_root', 'model_root'), map(str, roots)))
                config.update(fingerprint='c'*64, languages={'instagram':'pt'})
                video = service.artifacts.put(b'video')
                subject = service.artifacts.put(json.dumps({'assets':[{'kind':'mp4', 'artifact_ref':video,
                    'channel':'instagram', 'duration_seconds':1, 'audio_streams':[{'codec':'aac'}]}]}).encode())
                with patch('runner.audio_transcriber.fingerprint', return_value='c'*64), patch.object(AudioTranscriber, '_ffmpeg_hash', return_value='d'*64):
                    transcriber = AudioTranscriber(service, config)
                    service.audio_transcriber = transcriber
                    with self.assertRaises(AudioTranscriptionError):
                        transcriber.transcribe('run', claim, subject, video, 'instagram')
                    service.approve('controller', 'audio_policy', transcriber.policy_hash, 'operator')
                    def execute(supervisor, run, workspace, argv, deadline, limit, record, read_dirs):
                        scopes = LocalScopes(service.state_db_path, backend)
                        unit = scopes.reserve(run); record(unit); backend.unit = unit; backend.state = 'active'
                        scopes.bind(run, unit)
                        with wave.open(str(Path(workspace)/'audio.wav'), 'wb') as wav:
                            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                            wav.writeframes((500).to_bytes(2, 'little', signed=True)*16000)
                        audio = (Path(workspace)/'audio.wav').read_bytes()
                        report = {'language':'pt', 'audio_sha256':hashlib.sha256(audio).hexdigest(), 'duration':1,
                                  'peak':500, 'rms':500, 'segments':[]}
                        (Path(workspace)/'transcript.json').write_text(json.dumps(report))
                        scopes.stop(run, unit); scopes.close()
                        return {'outcome':'exited', 'exit_code':0, 'unit':unit}
                    with patch('runner.audio_transcriber.run_scoped_sandbox', side_effect=execute):
                        receipt = transcriber.transcribe('run', claim, subject, video, 'instagram')
                    self.assertTrue(transcriber.verify('run', subject, video, 'instagram', receipt))
                    self.assertFalse(transcriber.verify('run', subject, video, 'linkedin', receipt))
                    service.graph.db.execute("UPDATE local_scopes SET state='running' WHERE unit=(SELECT unit FROM local_audio_transcripts WHERE receipt_ref=?)", (receipt,))
                    self.assertFalse(transcriber.verify('run', subject, video, 'instagram', receipt))
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
