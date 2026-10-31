import os
import fcntl
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from runner.metrics_textfile import publish


class MetricsTextfileTests(unittest.TestCase):
    def test_concurrent_publication_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch('runner.metrics_textfile.read_metrics') as read:
                    with self.assertRaises(BlockingIOError): publish(root)
                    read.assert_not_called()
            finally: os.close(fd)

    def test_real_worker_publication_and_failed_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); data=root/'data'; data.mkdir(mode=0o700)
            output=root/'metrics'; output.mkdir(mode=0o700)
            secret=root/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            env={**os.environ,'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_DATA_DIR':str(data),'WORKFLOW_EXECUTION_MODE':'disabled'}
            env.pop('WORKFLOW_CONTROL_DIR',None)
            worker=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                path=data/'control.sock'; deadline=time.monotonic()+10
                while not path.exists() and worker.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                self.assertTrue(path.is_socket())
                result=subprocess.run([sys.executable,'-m','runner.metrics_textfile',str(output)],env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                old=(output/'workflow.prom').read_text()
                self.assertIn('workflow_snapshot_timestamp_seconds ',old)
                self.assertNotIn('x'*40,old+result.stdout+result.stderr)
            finally:
                worker.terminate(); worker.communicate(timeout=10)
            result=subprocess.run([sys.executable,'-m','runner.metrics_textfile',str(output)],env=env,capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((output/'workflow.prom').read_text(),old)

    def test_publishes_snapshot_privately_and_retains_last_success_on_failure(self):
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'workflow.prom'
            with patch('runner.metrics_textfile.read_metrics',return_value='workflow_snapshot_timestamp_seconds 123\n'):
                publish(root)
            self.assertEqual(target.read_text(),'workflow_snapshot_timestamp_seconds 123\n')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)
            with patch('runner.metrics_textfile.read_metrics',side_effect=RuntimeError('unavailable')):
                with self.assertRaises(RuntimeError): publish(root)
            self.assertEqual(target.read_text(),'workflow_snapshot_timestamp_seconds 123\n')
            with patch('runner.metrics_textfile.read_metrics',return_value='new 1\n'), patch('runner.metrics_textfile.os.replace',side_effect=OSError('unavailable')):
                with self.assertRaises(OSError): publish(root)
            self.assertEqual(target.read_text(),'workflow_snapshot_timestamp_seconds 123\n')
            self.assertEqual([p.name for p in Path(root).iterdir()],['workflow.prom'])

    def test_rejects_nonprivate_directory_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'metrics'; directory.mkdir(mode=0o755); directory.chmod(0o755)
            with patch('runner.metrics_textfile.read_metrics',return_value='metric 1\n'):
                with self.assertRaises(ValueError): publish(str(directory))
                link=Path(root)/'link'; link.symlink_to(directory,target_is_directory=True)
                with self.assertRaises((ValueError,OSError)): publish(str(link))
            self.assertEqual(list(directory.iterdir()),[])
