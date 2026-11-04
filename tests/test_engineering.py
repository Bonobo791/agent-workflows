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


STEP_SKILLS = {'01-plan': ['SKILL.md', 'REVIEW.md'], '02-instructions': ['SKILL.md', 'REVIEW.md'],
               '03-build': ['SKILL.md', 'REVIEW.md'], '04-pr': ['SKILL.md']}


def write_skill_files(root):
    for step, files in STEP_SKILLS.items():
        (root / step).mkdir(parents=True)
        for name in files:
            (root / step / name).write_text(f'Skill text for {step} {name}.\n')


class BuildTest(unittest.TestCase):
    """A real build (worktree included) made by `new` in a temporary target repository, with stub skill files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        repo = make_repo(root / 'repo')
        self.builds = root / 'builds'
        write_skill_files(root / 'skills')
        for name, value in (('BUILDS_DIR', self.builds), ('SKILLS_DIR', root / 'skills')):
            patch = mock.patch.object(eng, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        task = root / 'task.md'
        task.write_text(task_text(repo))
        with contextlib.redirect_stdout(io.StringIO()):
            eng.cmd_new('status-cost', str(task))
        self.build = self.builds / 'status-cost'
        self.step = self.build / '01-plan'
        self.step.mkdir()


def claude_reply(session_id, output='Done.', **extra):
    reply = {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': output,
             'structured_output': {'output': output}, 'session_id': session_id, 'num_turns': 2,
             'total_cost_usd': 0.25,
             'usage': {'input_tokens': 2, 'cache_creation_input_tokens': 4714,
                       'cache_read_input_tokens': 61972, 'output_tokens': 300},
             'permission_denials': []}
    reply.update(extra)
    return reply


def session_of(argv):
    flag = '--session-id' if '--session-id' in argv else '--resume'
    return argv[argv.index(flag) + 1]


def without_session_flags(argv):
    argv = list(argv)
    for flag in ('--session-id', '--resume'):
        if flag in argv:
            index = argv.index(flag)
            del argv[index:index + 2]
    return argv


def echo_ok(argv, prompt, cwd):
    return claude_reply(session_of(argv))


def failed_budget(argv, prompt, cwd):
    return 1, json.dumps(claude_reply(session_of(argv), is_error=True, subtype='error_max_budget_usd',
                                      errors=['Reached maximum budget ($5.00)']))


class FakeClaude:
    """Stands in for claude_process: returns queued replies in order and records every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, argv, prompt, cwd):
        self.calls.append((list(argv), prompt, cwd))
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(argv, prompt, cwd)
        if isinstance(reply, tuple):
            return reply
        return 0, json.dumps(reply)


class CallTest(BuildTest):
    def test_first_call_uses_session_id_then_resume(self):
        fake = FakeClaude(echo_ok, echo_ok)
        with mock.patch.object(eng, 'claude_process', fake):
            eng.call_claude(self.build, 'planner', 'first prompt', self.step, 'plan')
            eng.call_claude(self.build, 'planner', 'second prompt', self.step, 'revise')
        first, second = fake.calls[0][0], fake.calls[1][0]
        self.assertIn('--session-id', first)
        self.assertNotIn('--resume', first)
        self.assertIn('--resume', second)
        self.assertNotIn('--session-id', second)
        self.assertEqual(session_of(first), session_of(second))

    def test_every_call_of_a_session_has_the_same_flags(self):
        before = eng.claude_args(self.build, 'builder')
        fake = FakeClaude(echo_ok)
        with mock.patch.object(eng, 'claude_process', fake):
            eng.call_claude(self.build, 'builder', 'prompt', self.step, 'phase-1')
        after = eng.claude_args(self.build, 'builder')
        self.assertNotEqual(before, after)
        self.assertEqual(without_session_flags(before), without_session_flags(after))
        self.assertIn('Bash(python3 -m unittest discover -s tests)', after[after.index('--allowedTools') + 1])

    def test_prompt_goes_on_stdin_not_argv(self):
        argv = ['claude', '-p']
        done = subprocess.CompletedProcess(argv, 0, '{}', '')
        with mock.patch.object(eng, 'run_command', return_value=done) as run:
            eng.claude_process(argv, 'SECRET PROMPT TEXT', self.build / 'worktree')
        args, kwargs = run.call_args
        self.assertEqual(kwargs['input'], 'SECRET PROMPT TEXT')
        self.assertNotIn('SECRET PROMPT TEXT', args[0])

    def test_reply_that_is_not_json_is_an_error(self):
        fake = FakeClaude((0, 'No conversation found with session ID: x\n'))
        with mock.patch.object(eng, 'claude_process', fake):
            with self.assertRaisesRegex(eng.WorkflowError, 'did not return JSON'):
                eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan')

    def test_budget_reply_is_an_error_and_still_recorded(self):
        fake = FakeClaude(failed_budget)
        with mock.patch.object(eng, 'claude_process', fake):
            with self.assertRaisesRegex(eng.WorkflowError, 'error_max_budget_usd'):
                eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan')
        self.assertEqual(len((self.step / 'usage.jsonl').read_text().splitlines()), 1)

    def test_session_marked_started_only_after_success(self):
        fake = FakeClaude(failed_budget, echo_ok)
        with mock.patch.object(eng, 'claude_process', fake):
            with self.assertRaises(eng.WorkflowError):
                eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan')
            self.assertFalse(eng.load_sessions(self.build)['planner']['started'])
            eng.call_claude(self.build, 'planner', 'prompt', self.step, 'revise')
        self.assertTrue(eng.load_sessions(self.build)['planner']['started'])

    def test_unfinished_first_call_gets_a_new_session_id(self):
        fake = FakeClaude(failed_budget, echo_ok)
        with mock.patch.object(eng, 'claude_process', fake):
            with self.assertRaises(eng.WorkflowError):
                eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan')
            first_id = eng.load_sessions(self.build)['planner']['id']
            with self.assertLogs('engineering', level='INFO') as logs:
                eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan-again')
        self.assertTrue(any('starting a new planner session because the first call never finished' in line
                            for line in logs.output))
        second_argv = fake.calls[1][0]
        self.assertIn('--session-id', second_argv)
        self.assertNotEqual(session_of(second_argv), first_id)


class UsageTest(BuildTest):
    def test_usage_line_shows_non_cached_and_hit_rate(self):
        line = eng.usage_line({'input': 2, 'cache_write': 4714, 'cache_read': 61972, 'output': 300, 'cost_usd': 0.5})
        self.assertIn('non-cached input 4716', line)
        self.assertIn('93% of input', line)

    def test_status_adds_every_call(self):
        step3 = self.build / '03-build'
        step3.mkdir()
        fake = FakeClaude(echo_ok, echo_ok)
        with mock.patch.object(eng, 'claude_process', fake):
            eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan')
            eng.call_claude(self.build, 'builder', 'prompt', step3, 'phase-1')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            eng.cmd_status('status-cost')
        rows = {line.split()[0]: line for line in out.getvalue().splitlines() if line.strip()}
        self.assertIn('non-cached input 9432', rows['total'])
        self.assertIn('cost $0.5000', rows['total'])
        self.assertIn('non-cached input 4716', rows['planner'])
        self.assertIn('non-cached input 4716', rows['builder'])


REVIEW = {'verdict': 'revise', 'summary': 'Two things.',
          'findings': [{'severity': 'blocking', 'file': 'a.py', 'issue': 'Missing check.', 'fix': 'Add it.'}]}


class PromptTest(BuildTest):
    def write_phases(self):
        (self.build / '02-instructions').mkdir()
        (self.build / '02-instructions' / 'output.md').write_text('## Phase 1: First\nCommit message: Add first\n')
        (self.build / '02-instructions' / 'phases.json').write_text(
            json.dumps([{'n': 1, 'title': 'First', 'message': 'Add first'}]))

    def test_phase_prompt_is_short(self):
        prompt = eng.phase_prompt(2, 'Add the second thing')
        self.assertEqual(prompt, eng.PHASE_REQUEST.format(n=2, message='Add the second thing'))
        self.assertNotIn('Skill text', prompt)

    def test_prompts_start_with_the_skill(self):
        self.write_phases()
        for prompt in (eng.plan_prompt(self.build), eng.first_phase_prompt(self.build),
                       eng.review_text_prompt(self.build, '01-plan', 'the plan')):
            self.assertTrue(prompt.startswith('<skill>'), prompt[:60])

    def test_reviewer_gets_the_task_only_once(self):
        self.assertIn('<task>', eng.review_text_prompt(self.build, '01-plan', 'the plan'))
        sessions = eng.load_sessions(self.build)
        sessions['reviewer']['started'] = True
        eng.save_sessions(self.build, sessions)
        self.assertNotIn('<task>', eng.review_text_prompt(self.build, '02-instructions', 'the instructions'))

    def test_prompts_hold_no_build_folder_path(self):
        self.write_phases()
        prompts = [
            eng.plan_prompt(self.build), eng.instructions_prompt(self.build), eng.first_phase_prompt(self.build),
            eng.phase_prompt(1, 'Add first'), eng.fix_prompt('check failed'), eng.review_fix_prompt(REVIEW, '', 1),
            eng.pr_prompt(None), eng.review_text_prompt(self.build, '01-plan', 'plan'),
            eng.rereview_text_prompt('plan'), eng.review_code_prompt(self.build, 'abc', 'def', 'ok'),
            eng.rereview_code_prompt('abc', 'def', 'ok'), eng.revision_prompt(None, '', ['missing']),
        ]
        for prompt in prompts:
            self.assertNotIn(str(self.build), prompt)

    def test_revision_prompt_includes_only_given_parts(self):
        self.assertEqual(eng.revision_prompt(None, '', []), eng.REVISION_RULE)
        both = eng.revision_prompt(REVIEW, 'Make it shorter.', [])
        self.assertIn('<review>', both)
        self.assertIn('<owner notes>', both)
        self.assertNotIn('<python checks>', both)
        checks = eng.revision_prompt(None, '', ['missing section'])
        self.assertIn('<python checks>', checks)
        self.assertNotIn('<review>', checks)
        self.assertNotIn('<owner notes>', checks)


if __name__ == '__main__':
    unittest.main()
