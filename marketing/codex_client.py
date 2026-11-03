"""Drive the real `codex app-server` over stdio for one-shot marketing stages."""
import json
import re
import subprocess

# Features that only add tools. A lean client turns them off for its own process.
LEAN_DISABLED_FEATURES = ['apps', 'plugins', 'browser_use', 'browser_use_external', 'computer_use',
                          'image_generation', 'in_app_browser', 'tool_suggest', 'skill_search', 'shell_tool']
PLAIN_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')  # the only MCP server names `-c` can turn off


class CodexError(RuntimeError):
    pass


class CodexClient:
    def __init__(self, codex='codex', stderr_path=None, lean=False):
        # stdout carries only JSON-RPC; stderr (MCP and tool logs) goes to a file, never the terminal.
        self.codex = codex
        self.stderr = open(stderr_path, 'w') if stderr_path else subprocess.DEVNULL
        self.proc = None
        self.next_id = 0
        self.loaded = set()  # thread ids this process has started or resumed
        try:
            self._start([])
            if lean:
                extra_args = self._lean_args()
                self._stop()
                self._start(extra_args)
        except BaseException:
            self.close()  # the context manager is never entered when the constructor raises
            raise

    def _start(self, extra_args):
        self.proc = subprocess.Popen([self.codex, 'app-server', '--listen', 'stdio://', *extra_args],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1)
        self.next_id = 0
        self._request('initialize', {'clientInfo': {'name': 'agent_workflows', 'title': 'Agent Workflows', 'version': '0.1.0'}})
        self._send({'method': 'initialized'})

    def _stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)

    def close(self):
        if self.proc is not None:
            self._stop()
        if hasattr(self.stderr, 'close'):
            self.stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _send(self, message):
        self.proc.stdin.write(json.dumps(message) + '\n')
        self.proc.stdin.flush()

    def _recv(self):
        line = self.proc.stdout.readline()
        if not line:
            raise CodexError('codex app-server closed')
        return json.loads(line)

    def _refuse_server_request(self, message):
        # Never grant an approval the harness did not request.
        self._send({'id': message['id'], 'error': {'code': -32601, 'message': 'Request not supported'}})
        raise CodexError(f"server requested unsupported action: {message.get('method')}")

    def _request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self._send({'id': request_id, 'method': method, 'params': params})
        while True:
            message = self._recv()
            if 'id' in message and 'method' in message:
                self._refuse_server_request(message)
            if message.get('id') == request_id:
                if 'error' in message:
                    raise CodexError(f"{method} failed: {message['error']}")
                return message['result']

    def enabled_mcp_servers(self):
        config = self._request('config/read', {})['config']
        return [name for name, settings in (config.get('mcp_servers') or {}).items() if settings.get('enabled') is not False]

    def _lean_args(self):
        args = []
        for name in self.enabled_mcp_servers():
            if not PLAIN_NAME_RE.match(name):
                raise CodexError(f'MCP server {name!r} cannot be turned off for one run; rename it in ~/.codex/config.toml '
                                 'to use only letters, digits, "-" and "_"')
            args += ['-c', f'mcp_servers.{name}.enabled=false']
        for feature in LEAN_DISABLED_FEATURES:
            args += ['--disable', feature]
        return args

    def models(self):
        """Map each model name to the reasoning efforts it supports."""
        models, cursor = {}, None
        while True:
            result = self._request('model/list', {'cursor': cursor})
            for item in result['data']:
                models[item['model']] = [option['reasoningEffort'] for option in item['supportedReasoningEfforts']]
            cursor = result['nextCursor']
            if cursor is None:
                return models

    def run_turn(self, prompt, model, effort, cwd, output_schema=None, images=(), web_search='disabled', thread_id=None, persist=False):
        config = {'web_search': web_search}
        if thread_id is None:
            thread = self._request('thread/start', {'model': model, 'cwd': cwd, 'ephemeral': not persist, 'sandbox': 'read-only', 'approvalPolicy': 'never', 'config': config})
            thread_id = thread['thread']['id']
            self.loaded.add(thread_id)
        elif thread_id not in self.loaded:
            self._request('thread/resume', {'threadId': thread_id, 'cwd': cwd, 'sandbox': 'read-only', 'approvalPolicy': 'never', 'config': config, 'excludeTurns': True})
            self.loaded.add(thread_id)
        content = [{'type': 'text', 'text': prompt}] + [{'type': 'localImage', 'path': str(path)} for path in images]
        params = {'threadId': thread_id, 'input': content, 'model': model, 'effort': effort, 'cwd': cwd, 'approvalPolicy': 'never'}
        if output_schema is not None:
            params['outputSchema'] = output_schema
        self.next_id += 1
        turn_request = self.next_id
        self._send({'id': turn_request, 'method': 'turn/start', 'params': params})
        messages, usage, status, error = [], None, None, None
        while True:
            message = self._recv()
            if 'id' in message and 'method' in message:
                self._refuse_server_request(message)
            if message.get('id') == turn_request and 'error' in message:
                raise CodexError(f"turn/start failed: {message['error']}")
            method = message.get('method')
            if method == 'item/completed':
                item = message['params'].get('item', {})
                if item.get('type') == 'agentMessage' and item.get('text'):
                    messages.append(item['text'])
            elif method == 'thread/tokenUsage/updated' and message['params']['threadId'] == thread_id:
                usage = message['params']['tokenUsage']['total']
            elif method == 'turn/completed':
                turn = message['params']['turn']
                status = turn.get('status')
                error = turn.get('error')
                break
        if status != 'completed':
            raise CodexError(f'turn ended with status {status!r}: {error}')
        if usage is None:
            raise CodexError('turn completed without a thread/tokenUsage/updated notification')
        return {'thread_id': thread_id, 'text': messages[-1] if messages else '', 'usage': usage}
