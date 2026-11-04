import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# Loaded by path: tests/test_run.py already imports marketing/run.py as `run`.
spec = importlib.util.spec_from_file_location('engineering_run', ROOT / 'engineering' / 'run.py')
eng = importlib.util.module_from_spec(spec)
sys.modules['engineering_run'] = eng
spec.loader.exec_module(eng)

CLAUDE_VERSION = '2.1.318 (Claude Code)'


def help_text(omit=None):
    return '\n'.join(flag for flag in eng.REQUIRED_FLAGS if flag != omit)


def tool_outputs(help_stdout):
    """Fake subprocess.run that answers the preflight commands."""
    def fake(argv, **kwargs):
        if argv[:2] == ['claude', '--version']:
            return subprocess.CompletedProcess(argv, 0, CLAUDE_VERSION + '\n', '')
        if argv[:2] == ['claude', '--help']:
            return subprocess.CompletedProcess(argv, 0, help_stdout, '')
        return subprocess.CompletedProcess(argv, 0, 'ok\n', '')
    return fake


class PreflightTest(unittest.TestCase):
    def test_missing_flag_in_help_is_an_error(self):
        fake = tool_outputs(help_text(omit='--json-schema'))
        with mock.patch.object(eng.subprocess, 'run', side_effect=fake):
            with self.assertRaisesRegex(eng.WorkflowError, 'has no --json-schema; install Claude Code 2.1.318'):
                eng.check_tools()

    def test_missing_executable_is_a_workflow_error(self):
        with mock.patch.object(eng.subprocess, 'run', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(eng.WorkflowError, 'claude is not installed'):
                eng.check_tools()

    def test_schemas_serialize_the_same_every_time(self):
        for schema in (eng.WRITER_SCHEMA, eng.REVIEW_SCHEMA):
            self.assertEqual(eng.schema_text(schema), eng.schema_text(schema))


if __name__ == '__main__':
    unittest.main()
