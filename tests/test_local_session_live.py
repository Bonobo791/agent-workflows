"""Opt-in test of clean signed-in session recovery across real local scopes."""
import os
import tempfile
import time
import unittest
from pathlib import Path


def run_writer_turn(handle, model, session, prompt):
    from runner.protocol import AppServer
    chunks = []
    turns = []
    def event(message):
        if message.get('method') == 'item/agentMessage/delta':
            params = message.get('params', {})
            chunks.append((params.get('threadId'), params.get('turnId'), params.get('delta', '')))
    server = AppServer(handle.open_transport(time.monotonic() + 120, lambda: False), on_event=event, on_thread=handle.bind_session, on_turn=turns.append, before_thread=handle.verify_config)
    actual = server.run(prompt, model, thread_id=session, effort='medium')
    return actual, ''.join(text for thread,turn,text in chunks if thread == actual and turn == turns[0])


class SessionOutputTests(unittest.TestCase):
    def test_only_current_thread_and_turn_deltas_count_including_early_events(self):
        from tests.test_protocol import FakeTransport
        def delta(thread,turn,text):
            return {'method':'item/agentMessage/delta','params':{'threadId':thread,'turnId':turn,'delta':text}}
        class Handle:
            def open_transport(self,*args):
                return FakeTransport([{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':'current'}}},
                    delta('other','old','cobalt-orchid'),delta('current','new','early '),
                    {'id':3,'result':{'turn':{'id':'new'}}},delta('current','old','cobalt-orchid'),
                    delta('current','new','response'),{'method':'turn/completed','params':{'threadId':'current','turn':{'id':'new','status':'completed'}}}])
            def bind_session(self,session): pass
            def verify_config(self,request): return True
        session,output=run_writer_turn(Handle(),'model','current','prompt')
        self.assertEqual(session,'current')
        self.assertEqual(output,'early response')
        self.assertNotIn('cobalt-orchid',output)


@unittest.skipUnless(os.environ.get('CODEX_TEST_EXECUTABLE'), 'real local Codex executable not configured')
class LocalSessionLiveTests(unittest.TestCase):
    def test_writer_resumes_after_scope_termination(self):
        from runner.local_config_inspect import inspect_config
        from runner.local_process import start_local_process
        from runner.local_scope import SystemdScopes
        from runner.local_supervisor import LocalSupervisor
        from runner.model_policy import ModelPolicy
        from runner.protocol import AppServer

        environment = dict(os.environ)
        with tempfile.TemporaryDirectory(prefix='workflow-resume-') as root:
            workspace = Path(root) / 'workspace'
            workspace.mkdir(mode=0o700)
            db = str(Path(root) / 'state.db')
            config = inspect_config(environment['CODEX_TEST_EXECUTABLE'], str(workspace), environment)
            backend = SystemdScopes(environment)
            discovery = start_local_process(db, 'discovery', str(workspace), config['codex_executable'], environment, backend, lambda: True, config['disabled_integrations'])
            try:
                server = AppServer(discovery.open_transport(time.monotonic() + 90, lambda: False))
                server.initialize()
                self.assertTrue(discovery.verify_config(server._request))
                self.assertIsInstance(server._request('account/read', {'refreshToken': False}).get('account'), dict)
                model = ModelPolicy().discover(server._request)
            finally:
                discovery.finish()

            supervisor = LocalSupervisor(db, config['codex_executable'], environment, backend, lambda run: True, lambda run, role: str(workspace), lambda: True, config['disabled_integrations'])
            session = None
            prompts = ('Remember this exact token for the next turn: cobalt-orchid. Reply OK. Do not use tools.',
                       'Reply with only the exact token I asked you to remember. Do not use tools.')
            for prompt in prompts:
                handle = supervisor.start_execution('resume-probe', 'canonical_draft', session, {}, model)
                try:
                    actual, output = run_writer_turn(handle, model, session, prompt)
                    if session is not None:
                        self.assertEqual(actual, session)
                        self.assertIn('cobalt-orchid', output.lower())
                    session = actual
                    self.assertFalse(supervisor.can_resume('resume-probe', 'canonical_draft', session, {}, model))
                finally:
                    handle.finish()
                # Resumption requires an idle session and a verified terminated scope.
                self.assertTrue(supervisor.can_resume('resume-probe', 'canonical_draft', session, {}, model))
