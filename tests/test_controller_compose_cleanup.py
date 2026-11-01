import subprocess
import unittest
from unittest.mock import Mock, patch

from tests.test_controller_compose_integration import cleanup_compose


class ControllerComposeCleanupTests(unittest.TestCase):
    def test_failed_cleanup_retains_fixture_and_reports_error(self):
        temporary=Mock(); temporary.name='/private-fixture'
        with patch('tests.test_controller_compose_integration.subprocess.run',side_effect=subprocess.CalledProcessError(1,['compose'])):
            with self.assertRaises(subprocess.CalledProcessError) as error:
                cleanup_compose(['compose'],{},temporary)
        temporary.cleanup.assert_not_called()
        self.assertIn('/private-fixture',' '.join(error.exception.__notes__))

    def test_success_removes_fixture_after_compose_resources(self):
        temporary=Mock()
        with patch('tests.test_controller_compose_integration.subprocess.run') as run:
            cleanup_compose(['compose'],{},temporary)
        self.assertEqual(run.call_args.args[0],['compose','down','--volumes','--remove-orphans'])
        self.assertTrue(run.call_args.kwargs['check'])
        temporary.cleanup.assert_called_once()
