"""Engineering workflow.

task file -> plan -> phased instructions -> build (checks after every phase) -> draft pull request.
Every step: an agent writes its output, Python checks it, a reviewer session reviews it, then the owner approves or rejects.
Each build keeps three Claude Code sessions (planner, builder, reviewer) and resumes them, so each agent keeps what it has read.
"""
import argparse
import json
import logging
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SKILLS_DIR = HERE / 'skills'
BUILDS_DIR = REPO / 'builds'
STEP_IDS = ['01-plan', '02-instructions', '03-build', '04-pr']
SESSIONS = ['planner', 'builder', 'reviewer']
MAX_AUTO_REVISIONS = 2
MAX_FIX_ATTEMPTS = 2
CLAUDE_TIMEOUT = 3600
CHECK_TIMEOUT = 900
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')
TASK_SECTIONS = ['Repository', 'Base branch', 'Branch', 'Goal', 'Details', 'Done when', 'Check commands', 'Constraints']
PLAN_SECTIONS = ['Summary', 'Approach', 'Files to change', 'Tests', 'Risks', 'Open questions']
REQUIRED_FLAGS = ['--print', '--output-format', '--session-id', '--resume', '--json-schema', '--max-budget-usd',
                  '--permission-mode', '--tools', '--allowedTools', '--append-system-prompt', '--strict-mcp-config',
                  '--setting-sources', '--disable-slash-commands', '--exclude-dynamic-system-prompt-sections',
                  '--model', '--effort']

log = logging.getLogger('engineering')


class WorkflowError(Exception):
    pass


WRITER_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['output'],
                 'properties': {'output': {'type': 'string'}}}
REVIEW_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'summary', 'findings'],
    'properties': {
        'verdict': {'type': 'string', 'enum': ['approve', 'revise']},
        'summary': {'type': 'string'},
        'findings': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['severity', 'file', 'issue', 'fix'],
            'properties': {
                'severity': {'type': 'string', 'enum': ['blocking', 'minor']},
                'file': {'type': 'string'},
                'issue': {'type': 'string'},
                'fix': {'type': 'string'},
            }}},
    }}

# One dict literal: the owner changes models, effort and budgets here.
ROLES = {
    'planner': {
        'model': 'claude-opus-5-5', 'effort': 'high', 'budget_usd': 5.00,
        'tools': 'Read,Glob,Grep,Bash',
        'allowed': ['Read', 'Glob', 'Grep', 'Bash(git log *)', 'Bash(git show *)', 'Bash(git diff *)', 'Bash(git ls-files *)'],
        'schema': WRITER_SCHEMA,
    },
    'builder': {
        'model': 'claude-haiku-5-5', 'effort': 'high', 'budget_usd': 5.00,
        'tools': 'Read,Edit,Write,Glob,Grep,Bash',
        'allowed': ['Read', 'Edit', 'Write', 'Glob', 'Grep', 'Bash(git status *)', 'Bash(git diff *)', 'Bash(git log *)',
                    'Bash(git add *)', 'Bash(git commit *)'],
        'schema': WRITER_SCHEMA,
    },
    'reviewer': {
        'model': 'claude-opus-5-5', 'effort': 'medium', 'budget_usd': 3.00,
        'tools': 'Read,Glob,Grep,Bash',
        'allowed': ['Read', 'Glob', 'Grep', 'Bash(git diff *)', 'Bash(git log *)', 'Bash(git show *)'],
        'schema': REVIEW_SCHEMA,
    },
}

SYSTEM_RULES = {
    'planner': 'You plan software changes for an owner who approves every step. Read the code before you plan. Never invent facts about the code: if you did not read it, say so. No fallbacks: when something is unknown, list it under Open questions. Answer only through the output field.',
    'builder': 'You implement software changes phase by phase in this git worktree. Do exactly what the current phase says and nothing more. Commit each phase once, with exactly the commit message given. Never push, never change git config, never skip or disable a test. No fallbacks. Answer only through the output field with a short report.',
    'reviewer': 'You review plans, instructions and code for an owner who decides. Be specific: name the file and the line. Mark a finding blocking only when it is a bug, a missing requirement, a security problem or a broken test. Never invent facts about the code: read it first.',
}


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@contextmanager
def build_log(build, console=True):
    """Log lines go to build/run.log, and to the terminal too when console is true. A stop is logged, then re-raised."""
    handlers = [logging.FileHandler(build / 'run.log', encoding='utf-8')]
    if console:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        yield
    except BaseException as error:
        log.error('stopped: %s', error)
        raise
    finally:
        for handler in handlers:
            log.removeHandler(handler)
            handler.close()


def run_command(argv, **kwargs):
    """Run a program given as a list of arguments. A missing program is a WorkflowError."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, **kwargs)
    except FileNotFoundError:
        raise WorkflowError(f'{argv[0]} is not installed')


def schema_text(schema):
    return json.dumps(schema, separators=(',', ':'), sort_keys=True)


def check_tools():
    version = run_command(['claude', '--version'])
    if version.returncode != 0:
        raise WorkflowError(f'claude --version failed: {version.stderr.strip()}')
    version_text = version.stdout.strip()
    log.info('claude %s', version_text)
    help_text = run_command(['claude', '--help'])
    if help_text.returncode != 0:
        raise WorkflowError(f'claude --help failed: {help_text.stderr.strip()}')
    for flag in REQUIRED_FLAGS:
        if flag not in help_text.stdout:
            raise WorkflowError(f'claude {version_text} has no {flag}; install Claude Code 2.1.318 or newer')
    git = run_command(['git', '--version'])
    if git.returncode != 0:
        raise WorkflowError(f'git --version failed: {git.stderr.strip()}')
    log.info(git.stdout.strip())
    if run_command(['gh', 'auth', 'status']).returncode != 0:
        raise WorkflowError('gh is not signed in; run gh auth login')


def main():
    parser = argparse.ArgumentParser(description='Engineering workflow with an approval gate after every step.')
    sub = parser.add_subparsers(dest='command', required=True)
    new = sub.add_parser('new', help='create a build from a task file')
    new.add_argument('name')
    new.add_argument('task_file')
    run = sub.add_parser('run', help='run or continue a build')
    run.add_argument('name')
    status = sub.add_parser('status', help='show where a build is')
    status.add_argument('name')
    args = parser.parse_args()
    try:
        if args.command == 'new':
            cmd_new(args.name, args.task_file)
        elif args.command == 'run':
            cmd_run(args.name)
        else:
            cmd_status(args.name)
    except WorkflowError as error:
        raise SystemExit(f'error: {error}')


if __name__ == '__main__':
    main()
