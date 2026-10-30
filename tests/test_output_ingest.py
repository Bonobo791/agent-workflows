import os
from pathlib import Path
import tempfile
import unittest
from runner.artifacts import ArtifactStore
from runner.output_ingest import collect_outputs, OutputError

class OutputIngestTests(unittest.TestCase):
    def test_reads_confined_files_and_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as root:
            output=Path(root)/'out'; output.mkdir(); artifacts=Path(root)/'artifacts'; artifacts.mkdir(mode=0o700)
            (output/'nested').mkdir(); (output/'nested'/'copy').write_bytes(b'synthetic')
            store=ArtifactStore(str(artifacts)); ref=collect_outputs(output,['nested/copy'],store)[0]
            self.assertEqual(store.get(ref),b'synthetic')
            (output/'link').symlink_to(Path(root),target_is_directory=True)
            (output/'nested'/'filelink').symlink_to(output/'nested'/'copy')
            outside=Path(root)/'outside'; outside.write_bytes(b'private'); os.link(outside,output/'hardlink')
            for path in ('../outside','/etc/passwd','link/secret','nested/filelink','nested/../copy','hardlink'):
                with self.assertRaises(OutputError): collect_outputs(output,[path],store)
