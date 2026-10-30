"""Small synchronous app-server protocol client over an injected transport.

Transport must provide bounded send/receive and terminate on cancellation. Never
connect this client to a live process without the missing sandbox and auth gates.
"""
class ProtocolError(RuntimeError):
    pass

class RateLimited(ProtocolError):
    pass

class AppServer:
    def __init__(self, transport, on_event=None, on_thread=None, on_turn=None, before_turn=None):
        self.transport = transport
        self.next_id = 0
        self.on_event = on_event or (lambda _event: None)
        self.on_thread = on_thread or (lambda _thread: None)
        self.pending = []
        self.on_turn = on_turn or (lambda _turn: None)
        self.before_turn = before_turn or (lambda: None)

    def _request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self.transport.send({'id':request_id, 'method':method, 'params':params})
        while True:
            message = self.transport.receive()
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message or not isinstance(message.get('result'), dict): raise ProtocolError('app-server request failed')
                return message['result']
            self._notification(message)

    def _notification(self, message):
        if 'id' in message and 'method' in message:
            # An unexpected server-initiated action must never gain approval.
            self.transport.send({'id':message['id'], 'error':{'code':-32601,'message':'Request not supported'}})
            raise ProtocolError('server requested an unsupported approval or action')
        elif message.get('method'):
            if message['method']=='turn/completed': self.pending.append(message)
            self.on_event(message)

    def initialize(self):
        self._request('initialize', {'clientInfo':{'name':'agent_workflows','title':'Agent Workflows','version':'0.1.0'}})
        self.transport.send({'method':'initialized'})
    def discover_model(self,policy):
        self.initialize()
        return policy.discover(self._request)
    def run(self, prompt, model, thread_id=None, effort=None):
        self.initialize()
        if thread_id:
            thread = self._request('thread/resume', {'threadId':thread_id})
        else:
            thread = self._request('thread/start', {'model':model})
        try: tid = thread['thread']['id']
        except (KeyError, TypeError): raise ProtocolError('missing thread ID') from None
        if thread_id and tid != thread_id: raise ProtocolError('resumed unexpected thread')
        self.on_thread(tid)
        self.before_turn()
        params={'threadId':tid, 'input':[{'type':'text','text':prompt}]}
        if effort is not None: params.update({'model':model,'effort':effort})
        turn = self._request('turn/start', params)
        try: turn_id = turn['turn']['id']
        except (KeyError, TypeError): raise ProtocolError('missing turn ID') from None
        self.on_turn(turn_id)
        while True:
            message = self.pending.pop(0) if self.pending else self.transport.receive()
            if message.get('method') == 'turn/completed':
                params=message.get('params', {})
                result=params.get('turn', {})
                if params.get('threadId') != tid or result.get('id') != turn_id: continue
                if result.get('status') != 'completed':
                    error=result.get('error') or {}
                    info=error.get('codexErrorInfo') if isinstance(error,dict) else None
                    if info == 'UsageLimitExceeded' or (isinstance(info,dict) and 'UsageLimitExceeded' in info): raise RateLimited('subscription limit reached')
                    raise ProtocolError('turn did not complete')
                return tid
            self._notification(message)
