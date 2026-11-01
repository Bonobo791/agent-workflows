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

    def test_failed_fixture_setup_runs_cleanup(self):
        from tests.test_controller_compose_integration import ControllerComposeTests
        import tempfile
        from pathlib import Path
        created=[];real_temporary=tempfile.TemporaryDirectory;real_mkdtemp=tempfile.mkdtemp
        def legacy_directory(*args,**kwargs):
            if 'delete' in kwargs:raise TypeError('delete not supported')
            return real_temporary(*args,**kwargs)
        def temporary(*args,**kwargs):
            item=real_mkdtemp(*args,**kwargs);created.append(item);return item
        try:
            with patch.dict('os.environ',{'CONTROLLER_TEST_IMAGE':'synthetic','CONTROLLER_TEST_DOCKER_HOST':'unix:///synthetic'}),patch('tests.test_controller_compose_integration.tempfile.TemporaryDirectory',side_effect=legacy_directory),patch('tests.test_controller_compose_integration.tempfile.mkdtemp',side_effect=temporary),patch('pathlib.Path.write_text',side_effect=OSError('setup failed')),patch('tests.test_controller_compose_integration.subprocess.run') as run:
                fixture=ControllerComposeTests()
                with self.assertRaisesRegex(OSError,'setup failed'):
                    fixture.test_private_secret_and_state_survive_stop_and_start()
                run.assert_called_once()
                self.assertFalse(Path(created[0]).exists())
        finally:
            import shutil
            for item in created:shutil.rmtree(item,ignore_errors=True)
