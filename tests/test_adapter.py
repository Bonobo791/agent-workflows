import unittest
from runner.adapter import ExecutionDisabled, CodexAdapter

class AdapterTests(unittest.TestCase):
    def test_execution_is_disabled_until_auth_is_verified(self):
        with self.assertRaises(ExecutionDisabled): CodexAdapter().run('implement', 'task')

class ProviderTests(unittest.TestCase):
    def test_provider_uses_only_explicit_subscription_token(self):
        from runner.adapter import app_server_spec
        args, env = app_server_spec('synthetic-oauth', {'PATH':'/usr/bin', 'OPENAI_API_KEY':'should-not-be-passed'})
        self.assertEqual(env['ACCESS_TOKEN'], 'synthetic-oauth')
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertIn('shell_environment_policy.inherit="none"', args)
        self.assertIn('shell_environment_policy.ignore_default_excludes=false', args)
        self.assertIn('model_providers.openai_chatgpt_plan.requires_openai_auth=false', args)
        self.assertIn('model_providers.openai_chatgpt_plan.supports_websockets=false', args)
    def test_missing_token_is_blocked(self):
        from runner.adapter import app_server_spec
        with self.assertRaises(ExecutionDisabled): app_server_spec('', {})
    def test_provider_command_contains_no_token_and_delivery_defaults_closed(self):
        from runner.adapter import app_server_command, CredentialDelivery
        args=app_server_command()
        self.assertEqual(args[:4],['codex','app-server','--listen','stdio://'])
        self.assertIn('model_providers.openai_chatgpt_plan.env_key="ACCESS_TOKEN"',args)
        self.assertIn('model_providers.openai_chatgpt_plan.base_url="https://api.openai.com/v1"',args)
        self.assertIn('shell_environment_policy.inherit="none"',args)
        self.assertFalse(any('synthetic-oauth' in arg for arg in args))
        with self.assertRaises(ExecutionDisabled): CredentialDelivery().deliver('assignment')
