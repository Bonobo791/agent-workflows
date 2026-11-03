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

if __name__ == '__main__':
    unittest.main()
