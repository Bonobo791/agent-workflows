import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
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


GIT_IDENTITY = ['-c', 'user.name=test', '-c', 'user.email=test@example.com']


def git(repo, *args):
    done = subprocess.run(['git', *GIT_IDENTITY, '-C', str(repo), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return done.stdout.strip()


def make_repo(path):
    path.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(path)], check=True)
    (path / 'README.md').write_text('hello\n')
    git(path, 'add', 'README.md')
    git(path, 'commit', '-q', '-m', 'Initial commit')
    return path


def task_text(repo, branch='feature/status-cost', checks=('python3 -m unittest discover -s tests',)):
    commands = '\n'.join(f'- {command}' for command in checks)
    return f"""# Task: Test task

## Repository
{repo}

## Base branch
main

## Branch
{branch}

## Goal
Something is true.

## Details
Some details.

## Done when
- It works.

## Check commands
{commands}

## Constraints
None.
"""


class TaskTest(unittest.TestCase):
    def test_task_missing_section_is_an_error(self):
        text = task_text('/tmp/repo').replace('## Constraints\nNone.\n', '')
        self.assertIn('missing section "## Constraints"', eng.check_task(text))

    def test_task_sections_out_of_order_is_an_error(self):
        text = task_text('/tmp/repo').replace('## Base branch\nmain\n\n## Branch\nfeature/status-cost\n',
                                              '## Branch\nfeature/status-cost\n\n## Base branch\nmain\n')
        errors = eng.check_task(text)
        self.assertTrue(any('out of order' in error for error in errors), errors)

    def test_none_is_allowed_only_in_constraints(self):
        text = task_text('/tmp/repo').replace('Some details.', 'None.')
        self.assertTrue(any('"None." is allowed only' in error for error in eng.check_task(text)))

    def test_check_commands_are_split_with_shlex(self):
        text = task_text('/tmp/repo', checks=['python3 -m unittest discover -s "my tests"'])
        self.assertEqual(eng.check_commands(text), [['python3', '-m', 'unittest', 'discover', '-s', 'my tests']])
        self.assertEqual(eng.check_command_lines(text), ['python3 -m unittest discover -s "my tests"'])

    def test_unparsable_check_command_is_an_error(self):
        text = task_text('/tmp/repo', checks=['python3 -c "unbalanced'])
        with self.assertRaisesRegex(eng.WorkflowError, 'cannot be parsed'):
            eng.check_commands(text)


class NewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.repo = make_repo(self.root / 'repo')
        self.task = self.root / 'task.md'
        self.builds = self.root / 'builds'
        self.patch = mock.patch.object(eng, 'BUILDS_DIR', self.builds)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def new(self, name='status-cost', branch='feature/status-cost'):
        self.task.write_text(task_text(self.repo, branch=branch))
        with contextlib.redirect_stdout(io.StringIO()):
            eng.cmd_new(name, str(self.task))

    def test_new_creates_worktree_branch_and_sessions(self):
        self.new()
        build = self.builds / 'status-cost'
        worktree = build / 'worktree'
        self.assertTrue((worktree / 'README.md').is_file())
        self.assertEqual(git(worktree, 'rev-parse', '--abbrev-ref', 'HEAD'), 'feature/status-cost')
        self.assertEqual((build / 'base.txt').read_text().strip(), git(self.repo, 'rev-parse', 'main'))
        self.assertTrue((build / '00-task.md').is_file())
        sessions = json.loads((build / 'sessions.json').read_text())
        ids = [sessions[name]['id'] for name in eng.SESSIONS]
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(all(sessions[name]['started'] is False for name in eng.SESSIONS))

    def test_new_refuses_an_existing_branch(self):
        git(self.repo, 'branch', 'feature/status-cost')
        with self.assertRaisesRegex(eng.WorkflowError, 'branch feature/status-cost already exists'):
            self.new()
        self.assertFalse((self.builds / 'status-cost').exists())


if __name__ == '__main__':
    unittest.main()
