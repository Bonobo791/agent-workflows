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


USAGE_FIELDS = {'input': 'input_tokens', 'cache_write': 'cache_creation_input_tokens',
                'cache_read': 'cache_read_input_tokens', 'output': 'output_tokens'}


def allowed_tools(build, session_name):
    allowed = list(ROLES[session_name]['allowed'])
    if session_name == 'builder':
        allowed += [f'Bash({line})' for line in check_command_lines(read_task(build))]
    return allowed


def claude_args(build, session_name):
    """Every call of a session gets the same flags; only --session-id or --resume differs."""
    role = ROLES[session_name]
    entry = load_sessions(build)[session_name]
    argv = ['claude', '-p',
            '--output-format', 'json',
            '--model', role['model'],
            '--effort', role['effort'],
            '--max-budget-usd', f"{role['budget_usd']:.2f}",
            '--permission-mode', 'dontAsk',
            '--tools', role['tools'],
            '--allowedTools', ','.join(allowed_tools(build, session_name)),
            '--strict-mcp-config',
            '--setting-sources', 'project',
            '--disable-slash-commands',
            '--exclude-dynamic-system-prompt-sections',
            '--append-system-prompt', SYSTEM_RULES[session_name],
            '--json-schema', schema_text(role['schema'])]
    if entry['started']:
        return argv + ['--resume', entry['id']]
    return argv + ['--session-id', entry['id']]


def claude_process(argv, prompt, cwd):
    """The only place that starts claude. The prompt goes on stdin, never in argv."""
    try:
        done = run_command(argv, input=prompt, cwd=cwd, timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise WorkflowError(f'claude did not finish within {CLAUDE_TIMEOUT} s')
    if done.stderr.strip():
        with (Path(cwd).parent / 'claude-stderr.log').open('a', encoding='utf-8') as handle:
            handle.write(f'--- {now()}\n{done.stderr}')
    return done.returncode, done.stdout


def reply_field(reply, key):
    if key not in reply:
        raise WorkflowError(f'claude reply has no {key}')
    return reply[key]


def read_usage(paths):
    records = []
    for path in paths:
        if path.is_file():
            records += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return records


def record_usage(build, step_dir, session_name, what, reply):
    usage = reply_field(reply, 'usage')
    for key in USAGE_FIELDS.values():
        if key not in usage:
            raise WorkflowError(f'token usage has no {key}')
    record = {'time': now(), 'what': what, 'session': session_name,
              'session_id': reply_field(reply, 'session_id'), 'model': ROLES[session_name]['model'],
              'subtype': reply_field(reply, 'subtype'), 'turns': reply_field(reply, 'num_turns'),
              'cost_usd': reply_field(reply, 'total_cost_usd'),
              **{name: usage[key] for name, key in USAGE_FIELDS.items()},
              'denials': len(reply_field(reply, 'permission_denials'))}
    with (step_dir / 'usage.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record) + '\n')


def usage_totals(paths, session=None):
    """Adds up every call (every line) in the given usage files, optionally for one session only."""
    totals = {'input': 0, 'cache_write': 0, 'cache_read': 0, 'output': 0, 'cost_usd': 0.0}
    for record in read_usage(paths):
        if session is not None and record['session'] != session:
            continue
        for key in totals:
            totals[key] += record[key]
    return totals


def usage_line(totals):
    fresh, written, read = totals['input'], totals['cache_write'], totals['cache_read']
    seen = fresh + written + read
    hit = round(100 * read / seen) if seen else 0
    return (f"non-cached input {fresh + written} (input {fresh}, cache write {written}), "
            f"cache read {read} ({hit}% of input), output {totals['output']}, cost ${totals['cost_usd']:.4f}")


def session_attempted(build, session_name):
    """True when a call of this session was recorded, so its first call ran and did not succeed."""
    paths = [build / step / 'usage.jsonl' for step in STEP_IDS]
    return any(record['session'] == session_name for record in read_usage(paths))


def call_claude(build, session_name, prompt, step_dir, what):
    sessions = load_sessions(build)
    entry = sessions[session_name]
    if not entry['started'] and session_attempted(build, session_name):
        entry['id'] = str(uuid.uuid4())
        save_sessions(build, sessions)
        log.info('starting a new %s session because the first call never finished', session_name)
    calls = step_dir / 'calls'
    calls.mkdir(parents=True, exist_ok=True)
    stem = f'{len(list(calls.glob("*-prompt.txt"))) + 1:02d}-{what}'
    (calls / f'{stem}-prompt.txt').write_text(prompt)
    returncode, stdout = claude_process(claude_args(build, session_name), prompt, build / 'worktree')
    (calls / f'{stem}-reply.json').write_text(stdout)
    try:
        reply = json.loads(stdout)
    except json.JSONDecodeError:
        reply = None
    if not isinstance(reply, dict):
        raise WorkflowError(f"{what}: claude did not return JSON: {stdout[:300]!r}; saved to {calls / f'{stem}-reply.json'}")
    record_usage(build, step_dir, session_name, what, reply)
    if returncode != 0 or reply.get('is_error') or reply.get('subtype') != 'success':
        raise WorkflowError(f"{what}: claude ended with {reply.get('subtype')}: {reply.get('errors')}")
    if reply_field(reply, 'session_id') != entry['id']:
        raise WorkflowError(f"{what}: claude used session {reply['session_id']}, expected {entry['id']}")
    if not entry['started']:
        entry['started'] = True
        save_sessions(build, sessions)
    structured = reply_field(reply, 'structured_output')
    if not isinstance(structured, dict):
        raise WorkflowError(f'{what}: structured output is not an object')
    denials = reply_field(reply, 'permission_denials')
    for denial in denials:
        log.warning('%s: permission denied for %s %s', what, denial['tool_name'], denial['tool_input'])
    if denials:
        (calls / f'{stem}-denials.json').write_text(json.dumps(denials, indent=2) + '\n')
    return structured


def step_state(step_dir):
    if (step_dir / 'APPROVED').exists():
        return 'approved'
    if (step_dir / 'review.json').exists() or (step_dir / 'body.md').exists():
        return 'waiting for the owner'
    if step_dir.is_dir() and any(step_dir.iterdir()):
        return 'in progress'
    return 'not started'


def cmd_status(name):
    build = BUILDS_DIR / name
    if not build.is_dir():
        raise WorkflowError(f'no build named {name} in {BUILDS_DIR}')
    paths = [build / step / 'usage.jsonl' for step in STEP_IDS]
    print(f'Build {name}: {build}')
    print(f"{'step':<18}{'state':<24}usage")
    for step in STEP_IDS:
        print(f"{step:<18}{step_state(build / step):<24}{usage_line(usage_totals([build / step / 'usage.jsonl']))}")
    print()
    print(f"{'session':<18}{'':<24}usage")
    for session in SESSIONS:
        print(f"{session:<18}{'':<24}{usage_line(usage_totals(paths, session))}")
    print(f"{'total':<18}{'':<24}{usage_line(usage_totals(paths))}")
    print()
    print('Context sent on the last call of each session (input + cache write + cache read):')
    records = read_usage(paths)
    for session in SESSIONS:
        mine = [record for record in records if record['session'] == session]
        if not mine:
            print(f'  {session}: no calls yet')
            continue
        last = mine[-1]
        print(f"  {session}: {last['input'] + last['cache_write'] + last['cache_read']} tokens")


REVISION_RULE = 'This is a revision of your last answer in this session. Fix every item below. Return the complete new version in the output field, not a list of changes.'
PLAN_FORMAT = 'Write the plan in Markdown with exactly these ## sections, in this order: Summary, Approach, Files to change, Tests, Risks, Open questions. Read the code in the working directory before you write it.'
INSTRUCTIONS_REQUEST = 'The owner approved the last version of your plan above. Write the implementation instructions for it now.'
PHASE_REQUEST = 'Do Phase {n} of the instructions now, and only Phase {n}. When it is done, commit it once with exactly this message: {message}'
FIX_RULE = 'The checks below failed after your last commit. Fix the cause, run the same check, and commit the fix once with exactly this message: Fix failing checks. Do not change, skip or disable a test to make it pass.'
REVIEW_FIX_RULE = 'The reviewer found the problems below. Fix every blocking finding, then commit once with exactly this message: Fix review findings, round {round}'
PR_REQUEST = 'Write the pull request for all the work in this session. The first line of output is "# " and the title (at most 72 characters). The rest is the body, in Markdown, with ## Summary and ## Testing sections, and every ## heading of the pull request template if one is given.'
REVIEW_RULE = 'Review the deliverable against the task and the skill above. Return verdict, summary and findings.'
REREVIEW_RULE = 'This is the revised version of the deliverable you reviewed last. Check that every blocking finding from your last review is fixed, then review it again in full.'
CODE_REVIEW_REQUEST = 'Review the code change. Run git diff {base}...{head} and read the files you need. The plan and the instructions are the ones you reviewed earlier in this session.'
CODE_REREVIEW_REQUEST = 'New commits were added to fix your last review. Run git diff {previous}..{head}. Check every blocking finding from your last review is fixed and that nothing else broke.'


def block(name, content):
    return f'<{name}>\n{content.strip()}\n</{name}>'


def read_skill(step_id, filename):
    path = SKILLS_DIR / step_id / filename
    if not path.is_file():
        raise WorkflowError(f'missing skill file: {path}')
    return path.read_text()


def review_markdown(review):
    lines = [f"Verdict: {review['verdict']}", '', review['summary'], '']
    for number, finding in enumerate(review['findings'], 1):
        location = f"{finding['file']}: " if finding['file'] else ''
        lines.append(f"{number}. [{finding['severity']}] {location}{finding['issue']} Fix: {finding['fix']}")
    return '\n'.join(lines).rstrip() + '\n'


def phase_message(build, n):
    for phase in json.loads((build / '02-instructions' / 'phases.json').read_text()):
        if phase['n'] == n:
            return phase['message']
    raise WorkflowError(f'phase {n} is not in phases.json')


# Planner prompts. The planner session keeps the task and the plan it wrote.
def plan_prompt(build):
    return '\n\n'.join([block('skill', read_skill('01-plan', 'SKILL.md')), block('task', read_task(build)), PLAN_FORMAT])


def instructions_prompt(build):
    return '\n\n'.join([block('skill', read_skill('02-instructions', 'SKILL.md')), INSTRUCTIONS_REQUEST])


def revision_prompt(review, notes, errors):
    parts = [REVISION_RULE]
    if errors:
        parts.append(block('python checks', '\n'.join(errors)))
    if review is not None:
        parts.append(block('review', review_markdown(review)))
    if notes:
        parts.append(block('owner notes', notes))
    return '\n\n'.join(parts)


# Builder prompts. The first prompt carries the skill, the task and the instructions; later ones only name the phase.
def first_phase_prompt(build):
    return '\n\n'.join([block('skill', read_skill('03-build', 'SKILL.md')), block('task', read_task(build)),
                        block('instructions', (build / '02-instructions' / 'output.md').read_text()),
                        PHASE_REQUEST.format(n=1, message=phase_message(build, 1))])


def phase_prompt(n, message):
    return PHASE_REQUEST.format(n=n, message=message)


def fix_prompt(failures):
    return '\n\n'.join([FIX_RULE, block('check output', failures)])


def review_fix_prompt(review, notes, round_number):
    parts = [REVIEW_FIX_RULE.format(round=round_number), block('review', review_markdown(review))]
    if notes:
        parts.append(block('owner notes', notes))
    return '\n\n'.join(parts)


def pr_prompt(template):
    parts = [block('skill', read_skill('04-pr', 'SKILL.md'))]
    if template is not None:
        parts.append(block('pull request template', template))
    parts.append(PR_REQUEST)
    return '\n\n'.join(parts)


# Reviewer prompts. The reviewer gets the task once, with its first review.
def review_text_prompt(build, step_id, output):
    parts = [block('skill', read_skill(step_id, 'REVIEW.md'))]
    if not load_sessions(build)['reviewer']['started']:
        parts.append(block('task', read_task(build)))
    parts += [block('deliverable', output), REVIEW_RULE]
    return '\n\n'.join(parts)


def rereview_text_prompt(output):
    return '\n\n'.join([REREVIEW_RULE, block('deliverable', output)])


def review_code_prompt(build, base, head, checks):
    return '\n\n'.join([block('skill', read_skill('03-build', 'REVIEW.md')),
                        CODE_REVIEW_REQUEST.format(base=base, head=head), block('check output', checks)])


def rereview_code_prompt(previous, head, checks):
    return '\n\n'.join([CODE_REREVIEW_REQUEST.format(previous=previous, head=head), block('check output', checks)])


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
