import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from runner.plan_codex import CodexAppsPlan, PlanGatewayError


class CodexPlanTests(unittest.TestCase):
    def test_unverified_cleanup_cannot_return_a_successful_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            process=MagicMock(); process.pid=123; process.wait.side_effect=subprocess.TimeoutExpired('codex',2)
            server=MagicMock(); server._request.side_effect=[{}, {'thread':{'id':'thread'}}, {'isError':False,'structuredContent':{'ok':True,'data':{}}}]
            client=CodexAppsPlan('/usr/bin/codex',{'HOME':root},lambda:True,root)
            with patch('runner.plan_codex.subprocess.Popen',return_value=process), patch('runner.plan_codex.ProcessTransport'), patch('runner.plan_codex.AppServer',return_value=server), patch('runner.plan_codex.os.killpg'):
                with self.assertRaises(PlanGatewayError): client.get_issue('TOOLS',36)

    @unittest.skipUnless(os.environ.get('WORKFLOW_PLAN_LIVE_PROJECT') and os.environ.get('WORKFLOW_PLAN_LIVE_NUMBER'),'live read-only tracker test not configured')
    def test_signed_in_gateway_reads_issue_attachments_and_activity(self):
        from runner.local_config import local_supervisor_factory
        from runner.local_config_inspect import inspect_config
        project=os.environ['WORKFLOW_PLAN_LIVE_PROJECT']; number=int(os.environ['WORKFLOW_PLAN_LIVE_NUMBER'])
        with tempfile.TemporaryDirectory() as root:
            config=inspect_config(shutil.which('codex'),root,os.environ)
            supervisor=local_supervisor_factory(config,os.environ,root)(None)
            client=CodexAppsPlan(supervisor.executable,supervisor.environment,supervisor.verify_policy,root)
            issue=client.get_issue(project,number)['data']
            self.assertEqual(issue['identifier'].upper(),project.rsplit('.',1)[-1].upper()+'-'+str(number))
            self.assertIsInstance(client.call('list_attachments',{'issueId':issue['id']})['data'],list)
            activity=client.call('list_issue_activity',{'issueId':issue['id'],'filter':'comments','limit':100,'order':'asc'})['data']
            self.assertIsInstance(activity['items'],list)

    def test_gateway_errors_and_server_requests_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            executable=Path(root)/'codex'
            for output in ("{'id':m['id'],'result':{'isError':True,'structuredContent':{'ok':True}}}","{'id':99,'method':'approval/request','params':{}}"):
                executable.write_text('''#!/usr/bin/python3
import json, sys
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if m['method']=='initialize': result={}
 elif m['method']=='thread/start': result={'thread':{'id':'thread'}}
 else:
  print(json.dumps(%s),flush=True); continue
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
''' % output); executable.chmod(0o700)
                client=CodexAppsPlan(str(executable),{'HOME':root},lambda:True,root)
                with self.assertRaises(PlanGatewayError): client.get_issue('TOOLS',36)

    def test_hung_gateway_times_out_and_invalid_policy_never_launches(self):
        with tempfile.TemporaryDirectory() as root:
            executable=Path(root)/'codex'; executable.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(30)\n'); executable.chmod(0o700)
            client=CodexAppsPlan(str(executable),{'HOME':root},lambda:True,root)
            with patch.object(CodexAppsPlan,'TIMEOUT',.2):
                with self.assertRaises(PlanGatewayError): client.get_issue('TOOLS',36)
            client.verify_policy=lambda:False
            with patch('runner.plan_codex.subprocess.Popen') as launch:
                with self.assertRaises(PlanGatewayError): client.get_issue('TOOLS',36)
                launch.assert_not_called()

    def test_read_only_gateway_calls_bound_tool_without_inheriting_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            log=Path(root)/'calls'; executable=Path(root)/'codex'
            executable.write_text('''#!/usr/bin/python3
import json, os, sys
log=%r
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if m['method']=='initialize': result={}
 elif m['method']=='thread/start': result={'thread':{'id':'thread'}}
 elif m['method']=='mcpServer/tool/call':
  open(log,'w').write(json.dumps({'params':m['params'],'secret_inherited':'UNRELATED_SECRET' in os.environ}))
  result={'isError':False,'structuredContent':{'ok':True,'data':{'identifier':'TOOLS-36'}}}
 else: raise SystemExit(1)
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
''' % str(log)); executable.chmod(0o700)
            client=CodexAppsPlan(str(executable),{'HOME':root,'UNRELATED_SECRET':'private'},lambda:True,root)
            self.assertEqual(client.get_issue('TOOLS',36)['data']['identifier'],'TOOLS-36')
            record=json.loads(log.read_text())
            self.assertFalse(record['secret_inherited'])
            self.assertEqual(record['params']['server'],'codex_apps')
            self.assertEqual(record['params']['tool'],'it_s_a_plan_mcp.get_issue_by_number')
            self.assertEqual(record['params']['arguments'],{'projectKey':'TOOLS','sequenceNumber':36})
            with self.assertRaises(PlanGatewayError): client.call('update_issue',{})
            with self.assertRaises(PlanGatewayError): client.get_issue('bad..project',1)

