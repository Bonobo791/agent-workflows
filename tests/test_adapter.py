import unittest
from runner.adapter import ExecutionDisabled, CodexAdapter

class AdapterTests(unittest.TestCase):
    def test_execution_is_disabled_until_auth_is_verified(self):
        with self.assertRaises(ExecutionDisabled): CodexAdapter().run('implement', 'task')

class ProviderTests(unittest.TestCase):
    def test_managed_signin_spec_does_not_forward_credentials(self):
        import tempfile
        from pathlib import Path
        from runner.adapter import managed_app_server_spec
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'codex'; binary.write_text('synthetic'); binary.chmod(0o700)
            args,env=managed_app_server_spec(str(binary),{'HOME':tmp,'CODEX_HOME':tmp,'OPENAI_API_KEY':'secret','ACCESS_TOKEN':'secret','PATH':'untrusted'})
            self.assertEqual(env,{'HOME':tmp,'CODEX_HOME':tmp,'PATH':'/usr/local/bin:/usr/bin:/bin'})
            self.assertEqual(args[:4],[str(binary),'app-server','--listen','stdio://'])
            self.assertIn('default_permissions="workflow_local"',args)
            self.assertIn('permissions.workflow_local.network.enabled=false',args)
            self.assertIn('web_search="disabled"',args)
            self.assertIn('features.apps=false',args)
            self.assertIn('shell_environment_policy.inherit="none"',args)
            self.assertFalse(any('secret' in arg for arg in args))
            self.assertFalse(any('ACCESS_TOKEN' in arg for arg in args))
            self.assertTrue(any(str(binary) in arg and ':minimal' in arg and ':workspace_roots' in arg for arg in args))
            out=Path(tmp)/'out';out.mkdir(mode=0o700)
            args,_=managed_app_server_spec(str(binary),{'HOME':tmp},output_dir=str(out))
            self.assertTrue(any('"'+str(out)+'" = "write"' in arg for arg in args))
            repo=Path(tmp)/'repo';repo.mkdir(mode=0o700);gitdir=repo/'.git';gitdir.mkdir(mode=0o700)
            args,_=managed_app_server_spec(str(binary),{'HOME':tmp},output_dir=str(out),git_dir=str(gitdir))
            self.assertTrue(any('"'+str(gitdir)+'" = "write"' in arg for arg in args))
            args,_=managed_app_server_spec(str(binary),{'HOME':tmp},disabled_integrations={'mcp_servers':['server.with.dot'],'plugins':['plugin@market']})
            self.assertIn('mcp_servers={ "server.with.dot" = { enabled = false } }',args)
            self.assertIn('plugins={ "plugin@market" = { enabled = false } }',args)
    def test_managed_signin_rejects_unusable_paths(self):
        import tempfile
        from pathlib import Path
        from runner.adapter import managed_app_server_spec
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'codex'; binary.write_text('synthetic'); binary.chmod(0o700)
            for executable,environment in [('codex',{'HOME':tmp}),(str(binary),{'HOME':'relative'}),(str(binary),{'HOME':tmp,'CODEX_HOME':'relative'}),(tmp,{'HOME':tmp})]:
                with self.subTest(executable=executable,environment=environment), self.assertRaises(ExecutionDisabled): managed_app_server_spec(executable,environment)
    def test_scoped_read_roots_are_explicit_and_reject_broad_or_unsafe_paths(self):
        import tempfile
        from pathlib import Path
        from runner.adapter import managed_app_server_spec
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'codex';binary.write_text('synthetic');binary.chmod(0o700)
            allowed=root/'model';allowed.mkdir(mode=0o700)
            args,_=managed_app_server_spec(str(binary),{'HOME':tmp},read_dirs=[str(allowed)])
            self.assertTrue(any('"'+str(allowed)+'" = "read"' in item for item in args))
            with self.assertRaises(ExecutionDisabled):managed_app_server_spec(str(binary),{'HOME':tmp},read_dirs=[tmp])
            link=root/'link';link.symlink_to(allowed,target_is_directory=True)
            with self.assertRaises(ExecutionDisabled):managed_app_server_spec(str(binary),{'HOME':tmp},read_dirs=[str(link)])
            allowed.chmod(0o777)
            with self.assertRaises(ExecutionDisabled):managed_app_server_spec(str(binary),{'HOME':tmp},read_dirs=[str(allowed)])
    def test_managed_config_fails_closed_on_inherited_integrations_or_policy(self):
        from runner.adapter import verify_managed_config
        binary='/usr/bin/codex'
        config={'default_permissions':'workflow_local','approval_policy':'never',
                'permissions':{'workflow_local':{'extends':None,'workspace_roots':None,'filesystem':{':minimal':'read',':workspace_roots':'write',binary:'read','glob_scan_max_depth':None},'network':{'enabled':False}}},
                'features':{'hooks':False,'apps':False,'browser_use':False,'computer_use':False,'multi_agent':False,'goals':False,'memories':False},'web_search':'disabled','mcp_servers':{'a':{'enabled':False}},'plugins':{'b':{'enabled':False}},
                'shell_environment_policy':{'inherit':'none','set':{'PATH':'/usr/local/bin:/usr/bin:/bin'},'ignore_default_excludes':False}}
        self.assertTrue(verify_managed_config(config,binary))
        import copy
        separate=copy.deepcopy(config);separate['permissions']['workflow_local']['filesystem']['/private/output']='write'
        self.assertTrue(verify_managed_config(separate,binary,output_dir='/private/output'))
        with self.assertRaises(ExecutionDisabled): verify_managed_config(separate,binary)
        separate['permissions']['workflow_local']['filesystem']['/private/repo/.git']='write'
        self.assertTrue(verify_managed_config(separate,binary,output_dir='/private/output',git_dir='/private/repo/.git'))
        with self.assertRaises(ExecutionDisabled): verify_managed_config(separate,binary,output_dir='/private/output')
        for key, value in [('features',{'hooks':True}),('web_search','live'),('mcp_servers',{'new':{}}),('plugins',{'new':{'enabled':True}}),('approval_policy','on-request'),('shell_environment_policy',{'inherit':'all'}),('permissions',{})]:
            altered=copy.deepcopy(config); altered[key]=value
            with self.subTest(key=key):
                with self.assertRaises(ExecutionDisabled): verify_managed_config(altered,binary)
        for feature in config['features']:
            altered=copy.deepcopy(config); altered['features'][feature]=True
            with self.subTest(feature=feature), self.assertRaises(ExecutionDisabled): verify_managed_config(altered,binary)
    def test_managed_config_guard_reads_exact_workspace(self):
        from runner.adapter import verify_managed_workspace
        requests=[]
        def request(method,params): requests.append((method,params)); return {'config':{}}
        with self.assertRaises(ExecutionDisabled): verify_managed_workspace(request,'/workspace','/usr/bin/codex')
        self.assertEqual(requests,[('config/read',{'cwd':'/workspace','includeLayers':False})])
        with self.assertRaises(ExecutionDisabled): verify_managed_workspace(request,'relative','/usr/bin/codex')
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
        self.assertIn('shell_environment_policy.set.PATH="/usr/local/bin:/usr/bin:/bin"',args)
        self.assertFalse(any('synthetic-oauth' in arg for arg in args))
        with self.assertRaises(ExecutionDisabled): CredentialDelivery().deliver('assignment')
