import subprocess
import unittest
from unittest.mock import Mock, patch

from tests.monitoring_cleanup import cleanup_monitoring, stop_process


class MonitoringCleanupTests(unittest.TestCase):
    def test_failure_still_cleans_remaining_resources_and_preserves_evidence(self):
        process=Mock();process.poll.return_value=None
        server=Mock();thread=Mock();temporary=Mock(name='temporary');temporary.name='/fixture'
        def run(command,**kwargs):
            if command==['systemctl','--user','stop','second']:
                raise subprocess.TimeoutExpired(command,30)
        with patch('tests.monitoring_cleanup.subprocess.run',side_effect=run) as execute:
            with self.assertRaises(subprocess.TimeoutExpired) as failure:
                cleanup_monitoring(['first','second'],[process],server,thread,temporary,systemd=True)
        commands=[call.args[0] for call in execute.call_args_list]
        for name in ('first','second'):
            self.assertIn(['podman','rm','-f','--ignore',name],commands)
        process.terminate.assert_called_once();process.wait.assert_called_once_with(timeout=10)
        server.shutdown.assert_called_once();thread.join.assert_called_once();server.server_close.assert_called_once()
        temporary.cleanup.assert_not_called()
        self.assertIn('/fixture',' '.join(failure.exception.__notes__))

    def test_removal_failure_is_reported_and_keeps_private_fixture(self):
        temporary=Mock();temporary.name='/fixture';server=Mock();thread=Mock()
        with patch('tests.monitoring_cleanup.subprocess.run',side_effect=subprocess.CalledProcessError(1,['podman'])):
            with self.assertRaises(subprocess.CalledProcessError):
                cleanup_monitoring(['container'],[],server,thread,temporary)
        server.server_close.assert_called_once();temporary.cleanup.assert_not_called()

    def test_success_removes_fixture_after_resources(self):
        temporary=Mock()
        with patch('tests.monitoring_cleanup.subprocess.run') as execute:
            cleanup_monitoring(['container'],[],None,None,temporary)
        self.assertTrue(execute.call_args.kwargs['check'])
        temporary.cleanup.assert_called_once()

    def test_unresponsive_child_is_killed_and_reaped(self):
        process=Mock();process.poll.return_value=None
        process.wait.side_effect=[subprocess.TimeoutExpired('child',10),0]
        stop_process(process)
        process.kill.assert_called_once()
        self.assertEqual([call.kwargs['timeout'] for call in process.wait.call_args_list],[10,5])
