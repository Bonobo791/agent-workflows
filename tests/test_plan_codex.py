import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from runner.plan_codex import CodexAppsPlan, PlanGatewayError

def _group_members(group):
    result=[]
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields=path.read_text().rsplit(')',1)[1].split()
            if int(fields[2])==group and fields[0]!='Z': result.append((int(path.parent.name),fields[19]))
        except (OSError,ValueError,IndexError): pass
    return result


class CodexPlanTests(unittest.TestCase):
    def test_launcher_refuses_to_start_without_parent_death_fence(self):
        from runner import plan_codex_launcher
        libc=MagicMock();libc.prctl.return_value=-1
        with patch.object(sys,'argv',['launcher',str(os.getpid()),'/usr/bin/true']),patch.object(plan_codex_launcher.signal,'signal'),patch.object(plan_codex_launcher.ctypes,'CDLL',return_value=libc),patch.object(plan_codex_launcher.subprocess,'Popen') as launch:
            with self.assertRaises(OSError):plan_codex_launcher.main()
            launch.assert_not_called()

    def test_launcher_rejects_parent_lost_before_fence(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'started'; executable=Path(root)/'codex'
            executable.write_text('#!/usr/bin/python3\nfrom pathlib import Path\nPath(%r).write_text("started")\n' % str(marker)); executable.chmod(0o700)
            launcher=Path(__file__).resolve().parents[1]/'runner'/'plan_codex_launcher.py'
            process=subprocess.Popen([sys.executable,'-I',str(launcher),'1',str(executable)],start_new_session=True)
            self.assertEqual(process.wait(timeout=5),-signal.SIGKILL)
            self.assertFalse(marker.exists())

    def test_caller_crash_kills_gateway_that_ignores_pipe_closure(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); executable=root/'codex'
            executable.write_text('''#!/usr/bin/python3
import json,subprocess,sys,time
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if m['method']=='initialize': result={}
 elif m['method']=='thread/start': result={'thread':{'id':'thread'}}
 else:
  subprocess.Popen(['/usr/bin/sleep','30']); open(%r,'w').write('ready'); time.sleep(30); continue
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
''' % str(root/'ready')); executable.chmod(0o700)
            code='''
import os,sys
from pathlib import Path
import runner.plan_codex as gateway
root=sys.argv[1]; original=gateway.subprocess.Popen
def start(*args,**kwargs):
 p=original(*args,**kwargs); (Path(root)/'pid').write_text(str(p.pid)); return p
gateway.subprocess.Popen=start
gateway.CodexAppsPlan(str(Path(root)/'codex'),{'HOME':root},lambda:True,root).get_issue('TOOLS',34)
'''
            worker=subprocess.Popen([sys.executable,'-c',code,str(root)],cwd=str(Path(__file__).resolve().parents[1]),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            fd=None; pid=None; before=[]
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline and not (root/'ready').exists() and worker.poll() is None:time.sleep(.02)
                self.assertTrue((root/'ready').exists(),'gateway did not enter request')
                pid=int((root/'pid').read_text());fd=os.pidfd_open(pid)
                before=_group_members(pid);self.assertGreaterEqual(len(before),2)
                worker.kill();worker.wait(timeout=5)
                self.assertTrue(select.select([fd],[],[],3)[0],'gateway survived caller crash')
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and _group_members(pid):time.sleep(.02)
                self.assertEqual(_group_members(pid),[],'gateway descendants survived caller crash')
            finally:
                if worker.poll() is None:worker.kill();worker.wait(timeout=5)
                if fd is not None:
                    try:
                        if not select.select([fd],[],[],0)[0] and os.getpgid(pid)==pid:os.killpg(pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    finally:os.close(fd)
                for child,start in _group_members(pid) if pid is not None else []:
                    if (child,start) in before:
                        try:
                            child_fd=os.pidfd_open(child)
                            try:signal.pidfd_send_signal(child_fd,signal.SIGKILL)
                            finally:os.close(child_fd)
                        except ProcessLookupError:pass

    @unittest.skipUnless(os.environ.get('WORKFLOW_PLAN_LIVE_PROJECT') and os.environ.get('WORKFLOW_PLAN_LIVE_NUMBER'),'live read-only tracker test not configured')
    def test_signed_in_gateway_process_group_exits_after_caller_crash(self):
        code='''
import os,shutil,sys
from pathlib import Path
from runner.local_config import local_supervisor_factory
from runner.local_config_inspect import inspect_config
from runner.plan_codex import CodexAppsPlan
import runner.plan_codex as gateway
root=sys.argv[1]
config=inspect_config(shutil.which('codex'),root,os.environ)
supervisor=local_supervisor_factory(config,os.environ,root)(None)
client=CodexAppsPlan(supervisor.executable,supervisor.environment,supervisor.verify_policy,root)
original=gateway.subprocess.Popen
def start(*args,**kwargs):
 p=original(*args,**kwargs); (Path(root)/'gateway-pid').write_text(str(p.pid)); return p
gateway.subprocess.Popen=start
original_send=gateway.ProcessTransport.send
def send(self,message):
 original_send(self,message)
 if message.get('method')=='mcpServer/tool/call': (Path(root)/'call-started').write_text('started')
gateway.ProcessTransport.send=send
client.get_issue(os.environ['WORKFLOW_PLAN_LIVE_PROJECT'],int(os.environ['WORKFLOW_PLAN_LIVE_NUMBER']))
'''
        with tempfile.TemporaryDirectory() as root:
            cwd=Path(__file__).resolve().parents[1]
            worker=subprocess.Popen([sys.executable,'-c',code,root],cwd=str(cwd),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            fd=None; pid=None; before=[]
            try:
                marker=Path(root)/'gateway-pid'; deadline=time.monotonic()+20
                while time.monotonic()<deadline and not marker.exists() and worker.poll() is None: time.sleep(.02)
                self.assertTrue(marker.exists(),'gateway did not launch')
                pid=int(marker.read_text()); fd=os.pidfd_open(pid)
                marker=Path(root)/'call-started'; deadline=time.monotonic()+30
                while time.monotonic()<deadline and not marker.exists() and worker.poll() is None: time.sleep(.02)
                self.assertTrue(marker.exists(),'tracker call did not start')
                self.assertFalse(select.select([fd],[],[],0)[0],'gateway exited before crash')
                before=_group_members(pid); self.assertTrue(before)
                worker.kill(); worker.wait(timeout=5)
                self.assertEqual(worker.returncode,-signal.SIGKILL)
                self.assertTrue(select.select([fd],[],[],8)[0],'gateway survived caller crash')
                deadline=time.monotonic()+8
                while time.monotonic()<deadline and _group_members(pid): time.sleep(.05)
                self.assertEqual(_group_members(pid),[],'gateway process group survived caller crash')
            finally:
                if worker.poll() is None: worker.kill(); worker.wait(timeout=5)
                if fd is not None:
                    try:
                        if not select.select([fd],[],[],0)[0]:
                            if os.getpgid(pid)==pid: os.killpg(pid,signal.SIGKILL)
                            else: signal.pidfd_send_signal(fd,signal.SIGKILL)
                    except ProcessLookupError: pass
                    finally: os.close(fd)
                for child,start in _group_members(pid) if pid is not None else []:
                    if (child,start) in before:
                        try:
                            child_fd=os.pidfd_open(child)
                            try: signal.pidfd_send_signal(child_fd,signal.SIGKILL)
                            finally: os.close(child_fd)
                        except ProcessLookupError: pass

    def test_unverified_cleanup_cannot_return_a_successful_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            for failure in ('wait','signal','close'):
                with self.subTest(failure=failure):
                    process=MagicMock(); process.pid=123
                    if failure=='wait': process.wait.side_effect=subprocess.TimeoutExpired('codex',2)
                    if failure=='close': process.stdout.close.side_effect=OSError('close failed')
                    server=MagicMock(); server._request.side_effect=[{}, {'thread':{'id':'thread'}}, {'isError':False,'structuredContent':{'ok':True,'data':{}}}]
                    client=CodexAppsPlan('/usr/bin/codex',{'HOME':root},lambda:True,root)
                    with patch('runner.plan_codex.subprocess.Popen',return_value=process), patch('runner.plan_codex.ProcessTransport'), patch('runner.plan_codex.AppServer',return_value=server), patch('runner.plan_codex.os.killpg',side_effect=PermissionError() if failure=='signal' else None):
                        with self.assertRaises(PlanGatewayError): client.get_issue('TOOLS',36)
                    process.stdin.close.assert_called_once(); process.stdout.close.assert_called_once()

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

