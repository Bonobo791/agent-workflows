"""Start Claude Code for the marketing workflow and the issue importer. claude_process is the only place that starts it."""
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

SYSTEM_RULE = ('You do one step of a marketing workflow. Everything you need is in the prompt. '
               'Answer only through the structured output.')
TURN_TIMEOUT_SECONDS = 3600
PLAN_SERVER = "claude.ai It's a Plan - Project Management"
PLAN_TOOL = 'mcp__claude_ai_It_s_a_Plan_-_Project_Management__'
IMPORT_TOOLS = [PLAN_TOOL + name for name in ['get_issue_by_number', 'get_issue', 'get_document',
                                              'list_issue_documents', 'list_document_issues']]
REQUIRED_FLAGS = ['--print', '--output-format', '--verbose', '--model', '--effort', '--max-budget-usd', '--permission-mode',
                  '--tools', '--allowedTools', '--strict-mcp-config', '--setting-sources', '--disable-slash-commands',
                  '--exclude-dynamic-system-prompt-sections', '--append-system-prompt', '--json-schema', '--session-id', '--resume']
EFFORT_RE = re.compile(r'--effort <level>[^\n]*(?:\n\s*)?\(([^)]*)\)')  # the choices may wrap onto the next line


class ClaudeError(RuntimeError):
    """A call to claude that could not be used. run.py turns it into a WorkflowError with the step's name."""


def schema_text(schema):
    return json.dumps(schema, separators=(',', ':'), sort_keys=True)


def claude_argv(role, schema, tools, session, allowed=None, importer=False):
    """One call's flags. --tools is fixed per role; --allowedTools and the schema are the step's, so a role's calls share flags where they can."""
    argv = ['claude', '-p', '--output-format', 'stream-json', '--verbose', '--model', role['model'], '--effort', role['effort'],
            '--max-budget-usd', f"{role['budget_usd']:.2f}", '--permission-mode', 'dontAsk', '--tools', tools]
    if allowed:
        argv += ['--allowedTools', ','.join(allowed)]
    if not importer:
        argv.append('--strict-mcp-config')
    argv += ['--setting-sources', 'user,project' if importer else 'project', '--disable-slash-commands',
             '--exclude-dynamic-system-prompt-sections', '--append-system-prompt', SYSTEM_RULE,
             '--json-schema', schema_text(schema)]
    flag, session_id = session
    return argv + [flag, session_id]


def claude_process(argv, prompt, cwd, stderr_path=None):
    """The one place that starts claude. The prompt goes on stdin, never in argv."""
    if argv[1:] == ['--help']:  # claude exits before flushing a piped help text (cut at 16 KB), so its output goes to a file
        with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as out:
            try:
                done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.PIPE, text=True, cwd=str(cwd), timeout=TURN_TIMEOUT_SECONDS)
            except FileNotFoundError:
                raise ClaudeError('claude is not installed; install Claude Code 2.1.318 or newer') from None
            out.seek(0)
            return done.returncode, out.read()
    try:
        done = subprocess.run(argv, input=prompt, cwd=str(cwd), capture_output=True, text=True, timeout=TURN_TIMEOUT_SECONDS)
    except FileNotFoundError:
        raise ClaudeError('claude is not installed; install Claude Code 2.1.318 or newer') from None
    except subprocess.TimeoutExpired:
        raise ClaudeError(f'claude did not finish within {TURN_TIMEOUT_SECONDS} s') from None
    if stderr_path is not None and done.stderr.strip():
        with Path(stderr_path).open('a', encoding='utf-8') as handle:
            handle.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{done.stderr}")
    return done.returncode, done.stdout


def parse_events(stdout):
    """Each stdout line is one JSON object. Anything else is not a reply the workflow can use."""
    events = []
    for line in stdout.splitlines():
        if line.strip():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                raise ClaudeError('claude printed a line that is not JSON') from None
            if not isinstance(event, dict):
                raise ClaudeError('claude printed a line that is not a JSON object')
            events.append(event)
    return events


def effort_choices(help_text):
    """The values --effort accepts, read from its line in claude --help."""
    match = EFFORT_RE.search(help_text)
    if not match:
        raise ClaudeError('claude --help does not list the choices of --effort')
    return [choice.strip() for choice in match.group(1).split(',')]


class ClaudeClient:
    """Holds the function that starts claude, so a test can replace that function and keep the argv and parsing real."""

    def __init__(self, stderr_path=None, process=claude_process):
        self.stderr_path = stderr_path
        self.process = process

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def start(self, argv, prompt='', cwd=None):
        return self.process(argv, prompt, cwd or Path.cwd(), self.stderr_path)
