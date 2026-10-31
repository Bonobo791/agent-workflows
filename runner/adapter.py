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
        'shell_environment_policy.set.PATH="/usr/local/bin:/usr/bin:/bin"',
        'shell_environment_policy.ignore_default_excludes=false',
    ]
    args = ['codex', 'app-server', '--listen', 'stdio://']
    for setting in settings: args.extend(['-c', setting])
    return args

def managed_app_server_spec(executable, base_environment, *, disabled_integrations=None, output_dir=None, git_dir=None, read_dirs=None):
    """Build a local managed-auth launch spec; this does not verify isolation."""
    import json, os
    from pathlib import Path
    try:
        binary=Path(executable)
        if not binary.is_absolute() or not binary.is_file() or not os.access(binary,os.X_OK): raise ValueError()
        binary=binary.resolve(strict=True)
        home=base_environment['HOME']
        if not isinstance(home,str) or not Path(home).is_absolute() or not Path(home).is_dir(): raise ValueError()
        env={'HOME':home,'PATH':'/usr/local/bin:/usr/bin:/bin'}
        if 'CODEX_HOME' in base_environment:
            codex_home=base_environment['CODEX_HOME']
            if not isinstance(codex_home,str) or not Path(codex_home).is_absolute() or not Path(codex_home).is_dir(): raise ValueError()
            env['CODEX_HOME']=codex_home
    except (KeyError, TypeError, ValueError, OSError): raise ExecutionDisabled('managed Codex executable and absolute home directories required') from None
    output_rule=''
    if output_dir is not None:
        import stat
        try:
            path=Path(output_dir); info=path.lstat()
            if not path.is_absolute() or path.resolve(strict=True)!=path or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or any(ord(c)<32 for c in str(path)): raise ValueError()
            output_rule=', '+json.dumps(str(path))+' = "write"'
        except (OSError,TypeError,ValueError): raise ExecutionDisabled('private output directory required') from None
    git_rule=''
    if git_dir is not None:
        import stat
        try:
            path=Path(git_dir); info=path.lstat()
            if path.name!='.git' or not path.is_absolute() or path.resolve(strict=True)!=path or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or any(ord(c)<32 for c in str(path)): raise ValueError()
            git_rule=', '+json.dumps(str(path))+' = "write"'
        except (OSError,TypeError,ValueError): raise ExecutionDisabled('private Git directory required') from None
    read_rules=''
    if read_dirs is not None:
        import stat
        try:
            if not isinstance(read_dirs,list) or not 1<=len(read_dirs)<=4 or len(set(read_dirs))!=len(read_dirs): raise ValueError()
            home_path=Path(home).resolve(strict=True)
            paths=[]
            for value in read_dirs:
                path=Path(value);info=path.lstat()
                if not path.is_absolute() or path.resolve(strict=True)!=path or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022 or path==home_path or home_path.is_relative_to(path) or any(ord(c)<32 for c in str(path)): raise ValueError()
                if output_dir is not None and (path.is_relative_to(Path(output_dir)) or Path(output_dir).is_relative_to(path)): raise ValueError()
                if git_dir is not None and (path.is_relative_to(Path(git_dir)) or Path(git_dir).is_relative_to(path)): raise ValueError()
                paths.append(path)
            if any(a!=b and (a.is_relative_to(b) or b.is_relative_to(a)) for a in paths for b in paths): raise ValueError()
            read_rules=''.join(', '+json.dumps(str(path))+' = "read"' for path in paths)
        except (OSError,TypeError,ValueError): raise ExecutionDisabled('scoped read directories unavailable') from None
    settings=[
        'default_permissions="workflow_local"',
        'permissions.workflow_local.filesystem={ ":minimal" = "read", ":workspace_roots" = "write", '+json.dumps(str(binary))+' = "read"'+output_rule+git_rule+read_rules+' }',
        'permissions.workflow_local.network.enabled=false',
        'features.hooks=false',
        'features.apps=false',
        'features.browser_use=false',
        'features.computer_use=false',
        'features.multi_agent=false',
        'features.goals=false',
        'features.memories=false',
        'web_search="disabled"',
        'approval_policy="never"',
        'shell_environment_policy.inherit="none"',
        'shell_environment_policy.set.PATH="/usr/local/bin:/usr/bin:/bin"',
        'shell_environment_policy.ignore_default_excludes=false',
    ]
    if disabled_integrations is not None:
        if not isinstance(disabled_integrations,dict) or set(disabled_integrations)!={'mcp_servers','plugins'}: raise ExecutionDisabled('invalid integration configuration')
        for kind in ('mcp_servers','plugins'):
            names=disabled_integrations[kind]
            if not isinstance(names,list) or any(not isinstance(name,str) or not name or '\x00' in name for name in names) or len(set(names))!=len(names): raise ExecutionDisabled('invalid integration configuration')
            if names: settings.append(kind+'={ '+', '.join(json.dumps(name)+' = { enabled = false }' for name in names)+' }')
    args=[str(binary),'app-server','--listen','stdio://']
    for setting in settings: args.extend(['-c',setting])
    return args,env

def verify_managed_config(config, executable, *, output_dir=None, git_dir=None):
    """Reject inherited integrations and changes to the local sandbox policy."""
    try:
        profile=config['permissions']['workflow_local']
        filesystem={k:v for k,v in profile['filesystem'].items() if v is not None}
        network={k:v for k,v in profile['network'].items() if v is not None}
        shell=config['shell_environment_policy']
        expected_filesystem={':minimal':'read',':workspace_roots':'write',executable:'read'}
        if output_dir is not None: expected_filesystem[output_dir]='write'
        if git_dir is not None: expected_filesystem[git_dir]='write'
        valid=(config['default_permissions']=='workflow_local' and config['approval_policy']=='never'
            and profile.get('extends') is None and profile.get('workspace_roots') is None
            and filesystem==expected_filesystem
            and network=={'enabled':False} and network['enabled'] is False
            and all(config['features'].get(k) is False for k in ('hooks','apps','browser_use','computer_use','multi_agent','goals','memories'))
            and config['web_search']=='disabled'
            and shell['inherit']=='none' and shell['set']=={'PATH':'/usr/local/bin:/usr/bin:/bin'}
            and shell['ignore_default_excludes'] is False
            and all(isinstance(config[k],dict) and all(isinstance(v,dict) and v.get('enabled') is False for v in config[k].values()) for k in ('mcp_servers','plugins')))
        if valid: return True
    except (KeyError, TypeError, AttributeError): pass
    raise ExecutionDisabled('managed Codex configuration is not isolated')

def verify_managed_workspace(request, workspace, executable, *, output_dir=None, git_dir=None):
    from pathlib import Path
    if not callable(request) or not isinstance(workspace,str) or not Path(workspace).is_absolute(): raise ExecutionDisabled('absolute managed workspace required')
    try: result=request('config/read',{'cwd':workspace,'includeLayers':False})
    except Exception: raise ExecutionDisabled('managed Codex configuration unavailable') from None
    if not isinstance(result,dict): raise ExecutionDisabled('managed Codex configuration unavailable')
    return verify_managed_config(result.get('config'),executable,output_dir=output_dir,git_dir=git_dir)

class CredentialDelivery:
    """Trusted out-of-band delivery boundary; no production implementation."""
    def ready(self): return False
    def deliver(self,assignment,container_id=None):
        raise ExecutionDisabled('approved credential delivery unavailable')
    def verify_home(self,path): return False
