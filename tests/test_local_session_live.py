"""Opt-in test of clean signed-in session recovery across real local scopes."""
import os
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CODEX_TEST_EXECUTABLE'), 'real local Codex executable not configured')
class LocalSessionLiveTests(unittest.TestCase):
    def test_writer_resumes_after_scope_termination(self):
        from runner.local_config_inspect import inspect_config
        from runner.local_process import start_local_process
        from runner.local_scope import SystemdScopes
        from runner.local_supervisor import LocalSupervisor
        from runner.model_policy import ModelPolicy
        from runner.protocol import AppServer

        with tempfile.TemporaryDirectory(prefix='workflow-resume-') as root:
            workspace = Path(root) / 'workspace'
            workspace.mkdir(mode=0o700)
            db = str(Path(root) / 'state.db')
            config = inspect_config(os.environ['CODEX_TEST_EXECUTABLE'], str(workspace), os.environ)
            backend = SystemdScopes(os.environ)
            discovery = start_local_process(db, 'discovery', str(workspace), config['codex_executable'], os.environ, backend, lambda: True, config['disabled_integrations'])
            try:
                server = AppServer(discovery.open_transport(time.monotonic() + 90, lambda: False))
                server.initialize()
                self.assertTrue(discovery.verify_config(server._request))
                self.assertIsInstance(server._request('account/read', {'refreshToken': False}).get('account'), dict)
                model = ModelPolicy().discover(server._request)
            finally:
                discovery.finish()

            supervisor = LocalSupervisor(db, config['codex_executable'], os.environ, backend, lambda run: True, lambda run, role: str(workspace), lambda: True, config['disabled_integrations'])
            session = None
            prompts = ('Remember this exact token for the next turn: cobalt-orchid. Reply OK. Do not use tools.',
                       'Reply with only the exact token I asked you to remember. Do not use tools.')
            for prompt in prompts:
                handle = supervisor.start_execution('resume-probe', 'canonical_draft', session, {}, model)
                chunks = []
                def event(message):
                    if message.get('method') == 'item/agentMessage/delta':
                        chunks.append(message.get('params', {}).get('delta', ''))
                try:
                    server = AppServer(handle.open_transport(time.monotonic() + 120, lambda: False), on_event=event, on_thread=handle.bind_session, before_thread=handle.verify_config)
                    actual = server.run(prompt, model, thread_id=session, effort='medium')
                    if session is not None:
                        self.assertEqual(actual, session)
                        self.assertIn('cobalt-orchid', ''.join(chunks).lower())
                    session = actual
                finally:
                    handle.finish()
                self.assertTrue(supervisor.can_resume('resume-probe', 'canonical_draft', session, {}, model))
