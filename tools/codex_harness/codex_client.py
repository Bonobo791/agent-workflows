"""Drive the real `codex app-server` over stdio for one-shot marketing stages."""
import json
import subprocess


class CodexError(RuntimeError):
    pass


class CodexClient:
    def __init__(self, codex='codex', stderr_path=None):
        # stdout carries only JSON-RPC; stderr (MCP and tool logs) goes to a file, never the terminal.
        self.stderr = open(stderr_path, 'w') if stderr_path else subprocess.DEVNULL
        self.proc = subprocess.Popen([codex, 'app-server', '--listen', 'stdio://'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1)
        self.next_id = 0
        try:
            self._request('initialize', {'clientInfo': {'name': 'agent_workflows', 'title': 'Agent Workflows', 'version': '0.1.0'}})
            self._send({'method': 'initialized'})
        except BaseException:
            self.close()  # the context manager is never entered when the constructor raises
            raise

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)
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

    def models(self):
        return [item['model'] for item in self._request('model/list', {})['data']]

    def run_turn(self, prompt, model, cwd, output_schema=None, images=()):
        thread = self._request('thread/start', {'model': model, 'cwd': cwd, 'ephemeral': True, 'sandbox': 'read-only', 'approvalPolicy': 'never'})
        thread_id = thread['thread']['id']
        content = [{'type': 'text', 'text': prompt}] + [{'type': 'image', 'url': url} for url in images]
        params = {'threadId': thread_id, 'input': content, 'model': model, 'cwd': cwd, 'approvalPolicy': 'never'}
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
            elif method == 'thread/tokenUsage/updated':
                usage = message['params']
            elif method == 'turn/completed':
                turn = message['params']['turn']
                status = turn.get('status')
                error = turn.get('error')
                break
        if status != 'completed':
            raise CodexError(f'turn ended with status {status!r}: {error}')
        return {'thread_id': thread_id, 'text': messages[-1] if messages else '', 'usage': usage}
