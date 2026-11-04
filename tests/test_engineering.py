import contextlib
import importlib.util
import io
import itertools
import json
import shutil
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
        raise AssertionError(done.stdout + done.stderr)
    return done.stdout.strip()


def make_repo(path, files=None):
    path.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(path)], check=True)
    (path / 'README.md').write_text('hello\n')
    for name, text in (files or {}).items():
        (path / name).write_text(text)
    git(path, 'add', '-A')
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
    CHECKS = ('python3 -m unittest discover -s tests',)
    REPO_FILES = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        repo = make_repo(root / 'repo', self.REPO_FILES)
        self.builds = root / 'builds'
        write_skill_files(root / 'skills')
        for name, value in (('BUILDS_DIR', self.builds), ('SKILLS_DIR', root / 'skills')):
            patch = mock.patch.object(eng, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        task = root / 'task.md'
        task.write_text(task_text(repo, checks=self.CHECKS))
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

    def test_first_call_that_timed_out_gets_a_new_session_id(self):
        def timed_out(argv, prompt, cwd):
            raise eng.WorkflowError('claude did not finish within 3600 s')

        fake = FakeClaude(timed_out, echo_ok)
        with mock.patch.object(eng, 'claude_process', fake):
            with self.assertRaisesRegex(eng.WorkflowError, 'did not finish'):
                eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan')
            first_id = eng.load_sessions(self.build)['planner']['id']
            with self.assertLogs('engineering', level='INFO') as logs:
                eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan-again')
        second_argv = fake.calls[1][0]
        self.assertIn('--session-id', second_argv)
        self.assertNotEqual(session_of(second_argv), first_id)
        self.assertTrue(any('starting a new planner session' in line for line in logs.output))

    def test_first_call_with_non_json_gets_a_new_session_id(self):
        # The session already exists in Claude Code, so the second run must not reuse its id.
        fake = FakeClaude((1, 'Error: Session ID x is already in use.\n'), echo_ok)
        with mock.patch.object(eng, 'claude_process', fake):
            with self.assertRaisesRegex(eng.WorkflowError, 'did not return JSON'):
                eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan')
            first_id = eng.load_sessions(self.build)['planner']['id']
            self.assertTrue(eng.load_sessions(self.build)['planner']['attempted'])
            eng.call_claude(self.build, 'planner', 'prompt', self.step, 'plan-again')
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


GOOD_PLAN = '\n\n'.join(f'## {name}\nText for {name}.' for name in eng.PLAN_SECTIONS)
BAD_PLAN = '\n\n'.join(f'## {name}\nText for {name}.' for name in eng.PLAN_SECTIONS if name != 'Risks')
GOOD_INSTRUCTIONS = ('## Rules\nNo fallbacks.\n\n'
                     '## Phase 1: First\nDo the first thing.\nCommit message: Add the first thing\n\n'
                     '## Phase 2: Second\nDo the second thing.\nCommit message: Add the second thing\n')
BLOCKING = {'severity': 'blocking', 'file': '', 'issue': 'Too short.', 'fix': 'Make it longer.'}


class ByRole:
    """Routes each call to the queue of its session (planner, builder or reviewer) and records it."""

    def __init__(self, **queues):
        self.queues = {name: list(replies) for name, replies in queues.items()}
        self.calls = []

    def __call__(self, argv, prompt, cwd):
        role = next(name for name in eng.SESSIONS if eng.SYSTEM_RULES[name] in argv)
        self.calls.append((role, list(argv), prompt))
        reply = self.queues[role].pop(0)
        if callable(reply):
            reply = reply(argv, prompt, cwd)
        if isinstance(reply, tuple):
            return reply
        return 0, json.dumps(reply)


def writer(output):
    return lambda argv, prompt, cwd: claude_reply(session_of(argv), output=output)


def reviewer(verdict='approve', findings=()):
    structured = {'verdict': verdict, 'summary': 'Checked.', 'findings': list(findings)}
    return lambda argv, prompt, cwd: claude_reply(session_of(argv), output='review', structured_output=structured)


def answers(*values):
    iterator = iter(values)
    return lambda prompt: next(iterator)


class TextStepTest(BuildTest):
    def run_step(self, step_id, ask):
        with contextlib.redirect_stdout(io.StringIO()):
            eng.run_text_step(self.build, step_id, ask)

    def calls_by_role(self, fake):
        return [role for role, _, _ in fake.calls]

    def test_plan_check_failure_goes_back_without_reviewer(self):
        fake = ByRole(planner=[writer(BAD_PLAN), writer(GOOD_PLAN)], reviewer=[reviewer()])
        with mock.patch.object(eng, 'claude_process', fake):
            self.run_step('01-plan', answers('a'))
        self.assertEqual(self.calls_by_role(fake), ['planner', 'planner', 'reviewer'])
        self.assertIn('Risks', fake.calls[1][2])
        self.assertTrue((self.step / 'APPROVED').exists())

    def test_plan_revise_resumes_the_planner_session(self):
        fake = ByRole(planner=[writer(GOOD_PLAN), writer(GOOD_PLAN)],
                      reviewer=[reviewer('revise', [BLOCKING]), reviewer()])
        with mock.patch.object(eng, 'claude_process', fake):
            self.run_step('01-plan', answers('a'))
        self.assertEqual(self.calls_by_role(fake), ['planner', 'reviewer', 'planner', 'reviewer'])
        planner_first, planner_second = fake.calls[0][1], fake.calls[2][1]
        self.assertIn('--session-id', planner_first)
        self.assertIn('--resume', planner_second)
        self.assertIn(eng.REVISION_RULE, fake.calls[2][2])
        self.assertIn('Too short.', fake.calls[2][2])
        self.assertIn(eng.REREVIEW_RULE, fake.calls[3][2])

    def test_instructions_prompt_does_not_repeat_the_plan(self):
        fake = ByRole(planner=[writer(GOOD_PLAN), writer(GOOD_INSTRUCTIONS)], reviewer=[reviewer(), reviewer()])
        with mock.patch.object(eng, 'claude_process', fake):
            self.run_step('01-plan', answers('a'))
            self.run_step('02-instructions', answers('a'))
        instructions_prompt = fake.calls[2][2]
        self.assertIn(eng.INSTRUCTIONS_REQUEST, instructions_prompt)
        self.assertNotIn('Text for Summary.', instructions_prompt)

    def test_reviewer_uses_one_session_across_steps(self):
        fake = ByRole(planner=[writer(GOOD_PLAN), writer(GOOD_INSTRUCTIONS)], reviewer=[reviewer(), reviewer()])
        with mock.patch.object(eng, 'claude_process', fake):
            self.run_step('01-plan', answers('a'))
            self.run_step('02-instructions', answers('a'))
        reviewer_calls = [(argv, prompt) for role, argv, prompt in fake.calls if role == 'reviewer']
        self.assertEqual(session_of(reviewer_calls[0][0]), session_of(reviewer_calls[1][0]))
        self.assertIn('<task>', reviewer_calls[0][1])
        self.assertNotIn('<task>', reviewer_calls[1][1])

    def test_parse_phases_good_and_bad(self):
        phases = eng.parse_phases(GOOD_INSTRUCTIONS)
        self.assertEqual(phases, [{'n': 1, 'title': 'First', 'message': 'Add the first thing'},
                                  {'n': 2, 'title': 'Second', 'message': 'Add the second thing'}])
        bad = {
            'gap': '## Phase 1: A\nCommit message: a\n\n## Phase 3: B\nCommit message: b\n',
            'missing message': '## Phase 1: A\nDo it.\n',
            'duplicate message': '## Phase 1: A\nCommit message: same\n\n## Phase 2: B\nCommit message: same\n',
            'long message': '## Phase 1: A\nCommit message: ' + 'x' * 73 + '\n',
        }
        for label, text in bad.items():
            with self.subTest(label):
                with self.assertRaises(eng.WorkflowError):
                    eng.parse_phases(text)
                self.assertTrue(eng.check_instructions(text))

    def test_quit_at_the_gate_and_resume(self):
        fake = ByRole(planner=[writer(GOOD_PLAN)], reviewer=[reviewer()])
        with mock.patch.object(eng, 'claude_process', fake):
            with self.assertRaises(SystemExit):
                self.run_step('01-plan', answers('q'))
            self.assertTrue((self.step / 'review.json').exists())
            self.run_step('01-plan', answers('a'))
        self.assertEqual(self.calls_by_role(fake), ['planner', 'reviewer'])
        self.assertTrue((self.step / 'APPROVED').exists())

    def test_approved_instructions_write_phases_json(self):
        step = self.build / '02-instructions'
        fake = ByRole(planner=[writer(GOOD_INSTRUCTIONS)], reviewer=[reviewer()])
        with mock.patch.object(eng, 'claude_process', fake):
            self.run_step('02-instructions', answers('a'))
        phases = json.loads((step / 'phases.json').read_text())
        self.assertEqual([phase['message'] for phase in phases], ['Add the first thing', 'Add the second thing'])

    def test_text_gate_refuses_approval_while_checks_fail(self):
        fake = ByRole(planner=[writer(BAD_PLAN), writer(BAD_PLAN), writer(BAD_PLAN), writer(GOOD_PLAN)],
                      reviewer=[reviewer()])
        out = io.StringIO()
        with mock.patch.object(eng, 'claude_process', fake), contextlib.redirect_stdout(out):
            eng.run_text_step(self.build, '01-plan', answers('a', 'r', 'Add the risks section.', 'a'))
        self.assertIn('Check failed: missing section "## Risks"', out.getvalue())
        self.assertIn('Cannot approve while the checks above fail', out.getvalue())
        self.assertEqual(self.calls_by_role(fake), ['planner', 'planner', 'planner', 'planner', 'reviewer'])
        self.assertTrue((self.step / 'APPROVED').exists())

    def test_owner_reject_sends_the_notes(self):
        fake = ByRole(planner=[writer(GOOD_PLAN), writer(GOOD_PLAN)], reviewer=[reviewer(), reviewer()])
        with mock.patch.object(eng, 'claude_process', fake):
            self.run_step('01-plan', answers('r', 'Make the approach shorter.', 'a'))
        self.assertEqual(self.calls_by_role(fake), ['planner', 'reviewer', 'planner', 'reviewer'])
        revision = fake.calls[2][2]
        self.assertIn('<owner notes>\nMake the approach shorter.\n</owner notes>', revision)
        self.assertIn('<review>', revision)
        self.assertIn(eng.REREVIEW_RULE, fake.calls[3][2])


CHECK_SCRIPT = "import pathlib, sys\nsys.exit(0 if pathlib.Path('ready.txt').exists() else 1)\n"
PHASES = [{'n': 1, 'title': 'One', 'message': 'Add one'}, {'n': 2, 'title': 'Two', 'message': 'Add two'}]


WORK_FILES = itertools.count(1)


def builder(message, files=None):
    """A builder reply: writes a new file plus `files`, commits them once with `message`, and reports."""

    def reply(argv, prompt, cwd):
        worktree = Path(cwd)
        for name, text in {f'work-{next(WORK_FILES)}.txt': f'work {message}\n', **(files or {})}.items():
            (worktree / name).write_text(text)
        git(worktree, 'add', '-A')
        git(worktree, 'commit', '-q', '-m', message)
        return claude_reply(session_of(argv), output='Committed.')
    return reply


def builder_without_commit(files):
    """A builder reply that writes its files and reports, without committing."""
    def reply(argv, prompt, cwd):
        for name, text in files.items():
            (Path(cwd) / name).write_text(text)
        return claude_reply(session_of(argv), output='Written, not committed.')
    return reply


class BuildStepTest(BuildTest):
    CHECKS = ('python3 check.py',)
    REPO_FILES = {'check.py': CHECK_SCRIPT}

    def setUp(self):
        super().setUp()
        (self.build / '02-instructions').mkdir()
        (self.build / '02-instructions' / 'phases.json').write_text(json.dumps(PHASES))
        (self.build / '02-instructions' / 'output.md').write_text(GOOD_INSTRUCTIONS)

    def run_build(self, fake, *gate_answers):
        with mock.patch.object(eng, 'claude_process', fake), contextlib.redirect_stdout(io.StringIO()):
            eng.run_build_step(self.build, answers(*(gate_answers or ('a',))))

    def test_phase_without_the_expected_commit_goes_back_to_the_builder(self):
        fake = ByRole(builder=[builder('Add other', {'ready.txt': 'yes\n'}), builder('Add one'),
                               builder('Add two')],
                      reviewer=[reviewer()])
        self.run_build(fake)
        self.assertEqual(self.calls_by_role(fake), ['builder', 'builder', 'builder', 'reviewer'])
        self.assertIn(eng.PHASE_FIX_RULE.format(n=1, message='Add one'), fake.calls[1][2])
        self.assertEqual(fake.calls[2][2], eng.phase_prompt(2, 'Add two'))
        self.assertTrue((self.build / '03-build' / 'APPROVED').exists())

    def test_check_fix_before_the_phase_commit_asks_for_the_phase_message(self):
        fake = ByRole(builder=[builder_without_commit({'work.txt': 'half done\n'}),
                               builder('Add one', {'ready.txt': 'yes\n'}), builder('Add two')],
                      reviewer=[reviewer()])
        self.run_build(fake)
        self.assertEqual(self.calls_by_role(fake), ['builder', 'builder', 'builder', 'reviewer'])
        self.assertIn(eng.PHASE_FIX_RULE.format(n=1, message='Add one'), fake.calls[1][2])
        self.assertNotIn(eng.FIX_RULE, fake.calls[1][2])

    def test_phase_is_not_done_without_its_commit_message(self):
        worktree = self.build / 'worktree'
        phase_dir = self.build / '03-build' / 'phase-1'
        phase_dir.mkdir(parents=True)
        (phase_dir / 'start-head.txt').write_text(git(worktree, 'rev-parse', 'HEAD') + '\n')
        (phase_dir / 'report.md').write_text('Done.\n')
        (worktree / 'ready.txt').write_text('yes\n')
        git(worktree, 'add', 'ready.txt')
        git(worktree, 'commit', '-q', '-m', 'Add other')
        fake = ByRole(builder=[], reviewer=[])
        with self.assertRaisesRegex(eng.WorkflowError, "has no commit with the message 'Add one'"):
            self.run_build(fake)
        self.assertEqual(fake.calls, [])

    def calls_by_role(self, fake):
        return [role for role, _, _ in fake.calls]

    def test_uncommitted_changes_fail_verify(self):
        worktree = self.build / 'worktree'
        before = eng.head_sha(self.build)
        (worktree / 'ready.txt').write_text('yes\n')
        git(worktree, 'add', 'ready.txt')
        git(worktree, 'commit', '-q', '-m', 'Add one')
        (worktree / 'stray.txt').write_text('left behind\n')
        errors = eng.verify(self.build, before, 'Add one')
        self.assertTrue(any('uncommitted or untracked files' in error for error in errors), errors)

    def test_failed_check_goes_back_without_a_reviewer_call(self):
        fake = ByRole(builder=[builder('Add one'), builder('Fix failing checks', {'ready.txt': 'yes\n'}),
                               builder('Add two')],
                      reviewer=[reviewer()])
        self.run_build(fake)
        self.assertEqual(self.calls_by_role(fake), ['builder', 'builder', 'builder', 'reviewer'])
        self.assertIn(eng.FIX_RULE, fake.calls[1][2])
        self.assertIn('check failed (exit 1): python3 check.py', fake.calls[1][2])

    def test_phase_two_uses_the_short_prompt_and_resume(self):
        fake = ByRole(builder=[builder('Add one', {'ready.txt': 'yes\n'}), builder('Add two')], reviewer=[reviewer()])
        self.run_build(fake)
        second_argv, second_prompt = fake.calls[1][1], fake.calls[1][2]
        self.assertEqual(second_prompt, eng.phase_prompt(2, 'Add two'))
        self.assertIn('--resume', second_argv)

    def test_still_failing_after_fixes_stops_with_an_error(self):
        fake = ByRole(builder=[builder('Add one'), builder('Fix failing checks'), builder('Fix failing checks')],
                      reviewer=[])
        with self.assertRaisesRegex(eng.WorkflowError, 'phase 1 still fails its checks after 2 fixes'):
            self.run_build(fake)
        self.assertEqual(self.calls_by_role(fake), ['builder', 'builder', 'builder'])

    def test_blocking_review_sends_fixes_then_rereviews_the_new_range(self):
        fake = ByRole(builder=[builder('Add one', {'ready.txt': 'yes\n'}), builder('Add two'),
                               builder('Fix review findings, round 1')],
                      reviewer=[reviewer('revise', [BLOCKING]), reviewer()])
        self.run_build(fake)
        self.assertEqual(self.calls_by_role(fake), ['builder', 'builder', 'reviewer', 'builder', 'reviewer'])
        worktree = self.build / 'worktree'
        previous, head = git(worktree, 'rev-parse', 'HEAD~1'), git(worktree, 'rev-parse', 'HEAD')
        self.assertIn(f'{previous}..{head}', fake.calls[4][2])
        self.assertEqual((self.build / '03-build' / 'round.txt').read_text().strip(), '2')

    def test_build_resumes_at_the_first_unfinished_phase(self):
        first_run = ByRole(builder=[builder('Add one', {'ready.txt': 'yes\n'}), (1, 'boom')], reviewer=[])
        with self.assertRaises(eng.WorkflowError):
            self.run_build(first_run)
        second_run = ByRole(builder=[builder('Add two')], reviewer=[reviewer()])
        self.run_build(second_run)
        self.assertEqual(self.calls_by_role(second_run), ['builder', 'reviewer'])
        self.assertEqual(second_run.calls[0][2], eng.phase_prompt(2, 'Add two'))

    def test_check_timeout_is_a_failed_check(self):
        (self.build / '03-build').mkdir()
        timeout = subprocess.TimeoutExpired(cmd='python3 check.py', timeout=1)
        with mock.patch.object(eng, 'run_command', side_effect=timeout):
            errors = eng.run_checks(self.build)
        self.assertTrue(any('timed out' in error for error in errors), errors)
        self.assertEqual(len(list((self.build / '03-build').glob('checks-*.txt'))), 1)


GOOD_PR = '# Add the status total\n\n## Summary\nShows the total cost.\n\n## Testing\nUnit tests pass.\n'
BAD_PR = '# Add the status total\n\n## Summary\nShows the total cost.\n'


class PrCheckTest(unittest.TestCase):
    def test_pr_check_needs_title_summary_and_testing(self):
        self.assertEqual(eng.check_pr(GOOD_PR, None), [])
        self.assertTrue(any('"## Testing"' in error for error in eng.check_pr(BAD_PR, None)))
        self.assertTrue(any('first line' in error for error in eng.check_pr('## Summary\n\n## Testing\n', None)))
        long_title = '# ' + 'x' * 73 + '\n\n## Summary\n\n## Testing\n'
        self.assertTrue(any('73 characters' in error for error in eng.check_pr(long_title, None)))

    def test_pr_check_needs_the_template_headings(self):
        template = '## Summary\n\n## Checklist\n- [ ] tests\n'
        self.assertTrue(any('"## Checklist"' in error for error in eng.check_pr(GOOD_PR, template)))
        with_checklist = GOOD_PR + '\n## Checklist\n- [x] tests\n'
        self.assertEqual(eng.check_pr(with_checklist, template), [])


class PrStepTest(BuildTest):
    def setUp(self):
        super().setUp()
        self.published = []
        patch = mock.patch.object(eng, 'publish', side_effect=self.fake_publish)
        self.publish = patch.start()
        self.addCleanup(patch.stop)

    def fake_publish(self, build, title, body_file):
        self.published.append((title, body_file.read_text()))
        return 'https://github.com/Bonobo791/agent-workflows/pull/7\n'

    def test_pr_check_failure_goes_back_to_the_builder(self):
        fake = ByRole(builder=[writer(BAD_PR), writer(GOOD_PR)])
        with mock.patch.object(eng, 'claude_process', fake), contextlib.redirect_stdout(io.StringIO()):
            eng.run_pr_step(self.build, answers('a'))
        self.assertEqual([role for role, _, _ in fake.calls], ['builder', 'builder'])
        self.assertIn('<python checks>', fake.calls[1][2])
        self.assertEqual(self.published, [('Add the status total', GOOD_PR.split('\n', 1)[1].strip() + '\n')])

    def test_publish_runs_only_after_approval(self):
        fake = ByRole(builder=[writer(GOOD_PR)])
        with mock.patch.object(eng, 'claude_process', fake), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                eng.run_pr_step(self.build, answers('q'))
            self.publish.assert_not_called()
            eng.run_pr_step(self.build, answers('a'))
        self.publish.assert_called_once()
        self.assertEqual((self.build / '04-pr' / 'PR_URL').read_text().strip(),
                         'https://github.com/Bonobo791/agent-workflows/pull/7')
        self.assertTrue((self.build / '04-pr' / 'APPROVED').exists())

    def test_run_build_skips_approved_steps(self):
        for step in ['01-plan', '02-instructions', '03-build']:
            (self.build / step).mkdir(exist_ok=True)
            (self.build / step / 'APPROVED').touch()
        fake = ByRole(builder=[writer(GOOD_PR)])
        out = io.StringIO()
        with mock.patch.object(eng, 'claude_process', fake), contextlib.redirect_stdout(out):
            eng.run_build(self.build, answers('a'))
        self.assertEqual([role for role, _, _ in fake.calls], ['builder'])
        self.assertIn('All steps approved. Draft pull request: https://github.com/Bonobo791/agent-workflows/pull/7',
                      out.getvalue())


class SkillFileTest(unittest.TestCase):
    def test_shipped_skill_files_are_present(self):
        eng.check_skills()

    def test_missing_skill_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp) / 'skills'
            shutil.copytree(ROOT / 'engineering' / 'skills', skills)
            (skills / '03-build' / 'REVIEW.md').unlink()
            with mock.patch.object(eng, 'SKILLS_DIR', skills):
                with self.assertRaisesRegex(eng.WorkflowError, 'missing skill file'):
                    eng.check_skills()


REAL_PUBLISH = eng.publish  # PrStepTest replaces eng.publish; this keeps the real one for the test below


class PublishTest(BuildTest):
    def test_publish_without_a_url_is_a_workflow_error(self):
        (self.build / '04-pr').mkdir()
        body = self.build / '04-pr' / 'body.md'
        body.write_text('Body.\n')
        pushed = subprocess.CompletedProcess([], 0, '', '')
        created = subprocess.CompletedProcess([], 0, '\n', '')
        with mock.patch.object(eng, 'run_command', side_effect=[pushed, created]):
            with self.assertRaisesRegex(eng.WorkflowError, 'printed no pull request URL'):
                REAL_PUBLISH(self.build, 'Add one', body)


class RulesTest(unittest.TestCase):
    def test_role_rules_name_every_allowed_bash_command(self):
        for role in eng.SESSIONS:
            for entry in eng.ROLES[role]['allowed']:
                if not entry.startswith('Bash('):
                    continue
                command = ' '.join(entry[len('Bash('):-1].split()[:2])
                self.assertIn(command, eng.SYSTEM_RULES[role], f'the {role} rule does not name {command!r}')
        for role in eng.SESSIONS:
            self.assertIn('one command per Bash call', eng.SYSTEM_RULES[role])


if __name__ == '__main__':
    unittest.main()
