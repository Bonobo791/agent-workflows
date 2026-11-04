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


def parse_sections(md):
    """Return (heading, text) pairs for each '## ' heading, in file order, up to the next '## ' heading."""
    found, current, lines = [], None, []
    for line in md.splitlines():
        if line.startswith('## '):
            if current is not None:
                found.append((current, '\n'.join(lines).strip()))
            current, lines = line[3:].strip(), []
        elif current is not None:
            lines.append(line)
    if current is not None:
        found.append((current, '\n'.join(lines).strip()))
    return found


def task_value(text, section):
    return dict(parse_sections(text)).get(section, '')


def check_task(text):
    errors = []
    parsed = parse_sections(text)
    names = [name for name, _ in parsed]
    bodies = dict(parsed)
    for name in names:
        if name not in TASK_SECTIONS:
            errors.append(f'unknown section "## {name}"; use only these sections: {", ".join(TASK_SECTIONS)}')
        if names.count(name) > 1:
            errors.append(f'section "## {name}" appears more than once')
    for name in TASK_SECTIONS:
        if name not in names:
            errors.append(f'missing section "## {name}"')
    present = [name for name in names if name in TASK_SECTIONS]
    if present != [name for name in TASK_SECTIONS if name in present]:
        errors.append(f'sections are out of order; use this order: {", ".join(TASK_SECTIONS)}')
    for name in TASK_SECTIONS:
        body = bodies.get(name)
        if body is None:
            continue
        if not body:
            errors.append(f'section "## {name}" is empty')
        elif body == 'None.' and name != 'Constraints':
            errors.append(f'section "## {name}" needs content; "None." is allowed only in "## Constraints"')
    repository = bodies.get('Repository', '')
    if repository and not Path(repository).is_absolute():
        errors.append('section "## Repository" must be an absolute path')
    if 'Done when' in bodies and not any(line.startswith('- ') for line in bodies['Done when'].splitlines()):
        errors.append('section "## Done when" needs at least one "- " line')
    if 'Check commands' in bodies and not check_command_lines(text):
        errors.append('section "## Check commands" needs at least one "- " line')
    return errors


def check_command_lines(text):
    """The check commands exactly as the owner wrote them (without the leading '- ')."""
    body = task_value(text, 'Check commands')
    return [line[2:].strip() for line in body.splitlines() if line.startswith('- ')]


def check_commands(text):
    commands = []
    for line in check_command_lines(text):
        try:
            argv = shlex.split(line)
        except ValueError as error:
            raise WorkflowError(f'check command cannot be parsed: {line} ({error})')
        if not argv:
            raise WorkflowError('check command line is empty')
        commands.append(argv)
    return commands


def read_task(build):
    return (build / '00-task.md').read_text()


def load_sessions(build):
    return json.loads((build / 'sessions.json').read_text())


def save_sessions(build, sessions):
    (build / 'sessions.json').write_text(json.dumps(sessions, indent=2) + '\n')


def cmd_new(name, task_file):
    if not NAME_RE.match(name):
        raise WorkflowError(f'build name {name!r} must use lowercase letters, digits and hyphens')
    build = BUILDS_DIR / name
    if build.exists():
        raise WorkflowError(f'build {name} already exists: {build}')
    if not Path(task_file).is_file():
        raise WorkflowError(f'task file not found: {task_file}')
    text = Path(task_file).read_text()
    errors = check_task(text)
    if errors:
        for error in errors:
            print(f'error: {error}')
        raise WorkflowError('the task file has errors; fix them and run new again')
    repo = Path(task_value(text, 'Repository')).resolve()
    base = task_value(text, 'Base branch')
    branch = task_value(text, 'Branch')
    top = run_command(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'])
    if top.returncode != 0 or top.stdout.strip() != str(repo):
        raise WorkflowError(f'{repo} is not the top folder of a git checkout')
    if run_command(['git', '-C', str(repo), 'rev-parse', '--verify', '--quiet', f'{base}^{{commit}}']).returncode != 0:
        raise WorkflowError(f'base branch {base} does not exist in {repo}')
    if run_command(['git', '-C', str(repo), 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}']).returncode == 0:
        raise WorkflowError(f'branch {branch} already exists in {repo}')
    build.mkdir(parents=True)
    shutil.copyfile(task_file, build / '00-task.md')
    with build_log(build, console=False):
        worktree = build / 'worktree'
        added = run_command(['git', '-C', str(repo), 'worktree', 'add', '-b', branch, str(worktree), base])
        if added.returncode != 0:
            raise WorkflowError(f'git worktree add failed: {added.stderr.strip()}')
        log.info('created worktree %s on branch %s from %s', worktree, branch, base)
        head = run_command(['git', '-C', str(worktree), 'rev-parse', 'HEAD'])
        if head.returncode != 0:
            raise WorkflowError(f'git rev-parse failed in {worktree}: {head.stderr.strip()}')
        (build / 'base.txt').write_text(head.stdout.strip() + '\n')
        save_sessions(build, {name: {'id': str(uuid.uuid4()), 'started': False} for name in SESSIONS})
    print(f'Build folder: {build}')
    print(f'Next: python3 engineering/run.py run {name}')


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
