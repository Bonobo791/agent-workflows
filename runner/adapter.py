"""Execution boundary. No credential or billing fallback is configured."""
class ExecutionDisabled(RuntimeError):
    pass

class CodexAdapter:
    def run(self, role, inputs, session_id=None):
        raise ExecutionDisabled('Codex execution is disabled pending verified authentication and private deployment design')

def app_server_spec(access_token, base_environment):
    """Return documented process arguments and minimal environment; do not launch.

    The caller is responsible for validating authorization and protecting process
    isolation. This function does not establish that a token is valid.
    """
    if not isinstance(access_token, str) or not access_token:
        raise ExecutionDisabled('validated subscription token required')
    env = {key: base_environment[key] for key in ('PATH', 'HOME', 'LANG', 'TMPDIR') if key in base_environment}
    env['ACCESS_TOKEN'] = access_token
    return app_server_command(), env

def app_server_command():
    """Build the documented subscription provider command without credentials."""
    settings = [
        'model_provider="openai_chatgpt_plan"',
        'model_providers.openai_chatgpt_plan.name="ChatGPT plan"',
        'model_providers.openai_chatgpt_plan.base_url="https://api.openai.com/v1"',
        'model_providers.openai_chatgpt_plan.env_key="ACCESS_TOKEN"',
        'model_providers.openai_chatgpt_plan.wire_api="responses"',
        'model_providers.openai_chatgpt_plan.requires_openai_auth=false',
        'model_providers.openai_chatgpt_plan.supports_websockets=false',
        'shell_environment_policy.inherit="none"',
        'shell_environment_policy.ignore_default_excludes=false',
    ]
    args = ['codex', 'app-server', '--listen', 'stdio://']
    for setting in settings: args.extend(['-c', setting])
    return args

class CredentialDelivery:
    """Trusted out-of-band delivery boundary; no production implementation."""
    def ready(self): return False
    def deliver(self,assignment,container_id=None):
        raise ExecutionDisabled('approved credential delivery unavailable')
    def verify_home(self,path): return False
