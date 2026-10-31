import tempfile,unittest
from pathlib import Path
from runner.audio_runtime import AudioRuntimeError,fingerprint

class AudioRuntimeTests(unittest.TestCase):
    def test_fingerprint_changes_with_files_and_rejects_unsafe_roots(self):
        with tempfile.TemporaryDirectory() as root:
            paths=[]
            for name in ('runtime','python','model'):
                path=Path(root)/name;path.mkdir(mode=0o700);(path/'data').write_text(name);paths.append(str(path))
            first=fingerprint(paths);self.assertEqual(first,fingerprint(paths))
            (Path(paths[2])/'data').write_text('changed');self.assertNotEqual(first,fingerprint(paths))
            (Path(paths[1])/'data').chmod(0o666)
            with self.assertRaises(AudioRuntimeError):fingerprint(paths)
            with self.assertRaises(AudioRuntimeError):fingerprint([paths[0],paths[0],paths[2]])
