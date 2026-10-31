import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from runner.local_config_inspect import inspect_config, InspectionError


class LocalConfigInspectionTests(unittest.TestCase):
    def test_npm_launcher_resolves_and_pins_the_native_executable(self):
        import json
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); package=root/'node_modules'/'@openai'/'codex'; (package/'bin').mkdir(parents=True)
            (package/'package.json').write_text(json.dumps({'name':'@openai/codex'}))
            wrapper=package/'bin'/'codex.js'; wrapper.write_text('#!/usr/bin/env node\n'); wrapper.chmod(0o700)
            native=package/'node_modules'/'@openai'/'codex-linux-x64'/'vendor'/'x86_64-unknown-linux-musl'/'bin'/'codex'
            native.parent.mkdir(parents=True)
            native.write_text('''#!/usr/bin/python3
import json,sys
for line in sys.stdin:
    request=json.loads(line)
    if request.get("method")=="initialize": print(json.dumps({"id":request["id"],"result":{}}),flush=True)
    elif request.get("method")=="config/read": print(json.dumps({"id":request["id"],"result":{"config":{"mcp_servers":{},"plugins":{}}}}),flush=True)
''')
            native.chmod(0o700)
            launcher=root/'codex'; launcher.symlink_to(wrapper)
            with patch('platform.machine',return_value='x86_64'):
                result=inspect_config(str(launcher),str(root),{'HOME':str(root)})
            self.assertEqual(result['codex_executable'],str(native))
            self.assertEqual(result['codex_sha256'],hashlib.sha256(native.read_bytes()).hexdigest())
            native.chmod(0o777)
            with patch('platform.machine',return_value='x86_64'), self.assertRaises(InspectionError):
                inspect_config(str(launcher),str(root),{'HOME':str(root)})
            native.chmod(0o700)
            hoisted=root/'node_modules'/'@openai'/'codex-linux-x64'/'vendor'/'x86_64-unknown-linux-musl'/'bin'/'codex'
            hoisted.parent.mkdir(parents=True); native.rename(hoisted)
            with patch('platform.machine',return_value='x86_64'):
                self.assertEqual(inspect_config(str(launcher),str(root),{'HOME':str(root)})['codex_executable'],str(hoisted))

    def test_discovery_returns_only_hash_and_integration_names(self):
        with tempfile.TemporaryDirectory() as root:
            executable=Path(root)/'codex'
            executable.write_text('''#!/usr/bin/python3
import json,sys
for line in sys.stdin:
    request=json.loads(line)
    if request.get("method")=="initialize": print(json.dumps({"id":request["id"],"result":{}}),flush=True)
    elif request.get("method")=="config/read": print(json.dumps({"id":request["id"],"result":{"config":{"mcp_servers":{"private-server":{"token":"secret"}},"plugins":{"private-plugin":{"enabled":True}},"secret":"do-not-print"}}}),flush=True)
''')
            executable.chmod(0o700)
            result=inspect_config(str(executable),root,{'HOME':root})
            self.assertEqual(result,{'codex_executable':str(executable),'codex_sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),'disabled_integrations':{'mcp_servers':['private-server'],'plugins':['private-plugin']}})
            self.assertNotIn('secret',str(result))

    def test_rejects_unsafe_executable_and_discovery_directory(self):
        with tempfile.TemporaryDirectory() as root:
            executable=Path(root)/'codex';executable.write_text('#!/bin/sh\n');executable.chmod(0o777)
            with self.assertRaises(InspectionError):inspect_config(str(executable),root,{'HOME':root})
            executable.chmod(0o700);os.chmod(root,0o755)
            with self.assertRaises(InspectionError):inspect_config(str(executable),root,{'HOME':root})
            os.chmod(root,0o700)
            with self.assertRaises(InspectionError):inspect_config('relative-codex',root,{'HOME':root})
    def test_malformed_config_fails_without_exposing_server_output(self):
        with tempfile.TemporaryDirectory() as root:
            executable=Path(root)/'codex'
            executable.write_text('''#!/usr/bin/python3
import json,sys
for line in sys.stdin:
    request=json.loads(line)
    if request.get("method")=="initialize": print(json.dumps({"id":request["id"],"result":{}}),flush=True)
    elif request.get("method")=="config/read": print(json.dumps({"id":request["id"],"result":{"config":{"plugins":"secret-value"}}}),flush=True)
''')
            executable.chmod(0o700)
            with self.assertRaises(InspectionError) as caught:inspect_config(str(executable),root,{'HOME':root})
            self.assertNotIn('secret-value',str(caught.exception))
