import sys
import tempfile
import unittest
from runner.process import ManagedProcess

class ProcessTests(unittest.TestCase):
    def test_cancel_terminates_process_group(self):
        with tempfile.TemporaryDirectory() as root:
            process=ManagedProcess([sys.executable,'-c','import time; time.sleep(30)'],cwd=root,env={})
            process.cancel(timeout=1)
            self.assertIsNotNone(process.wait(timeout=2))
            process.cancel(timeout=1)
