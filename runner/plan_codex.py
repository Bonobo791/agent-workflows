"""Read-only It's a Plan access through the owner's signed-in Codex app gateway."""
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from runner.plan_mcp import PlanMCP
from runner.process_transport import ProcessTransport
from runner.protocol import AppServer

class PlanGatewayError(RuntimeError): pass

def _stop_gateway(process):
    if process is None: return
    cleanup_failed=False
    try: os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    except OSError: cleanup_failed=True
    try: process.wait(timeout=2)
    except subprocess.TimeoutExpired: cleanup_failed=True
    for stream in (process.stdin,process.stdout):
        try:
            if stream is not None: stream.close()
        except OSError: cleanup_failed=True
    if cleanup_failed: raise PlanGatewayError('tracker gateway cleanup unresolved')

class CodexAppsPlan:
    TIMEOUT=30
    def __init__(self,executable,environment,verify_policy,workspace):
        if not isinstance(executable,str) or not os.path.isabs(executable) or not callable(verify_policy) or not isinstance(workspace,str) or not os.path.isabs(workspace): raise PlanGatewayError('trusted Codex executable required')
        self.executable=executable; self.verify_policy=verify_policy; self.workspace=workspace
        self.environment={key:environment[key] for key in ('HOME','CODEX_HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if key in environment}
        if not os.path.isabs(self.environment.get('HOME','')): raise PlanGatewayError('private Codex home required')
        self.environment['PATH']='/usr/bin:/bin'
    def get_issue(self,project_key,number):
        if not isinstance(project_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?',project_key) or type(number) is not int or number<1: raise PlanGatewayError('invalid issue identifier')
        return self.call('get_issue_by_number',{'projectKey':project_key,'sequenceNumber':number})
    def call(self,name,arguments):
        if name not in PlanMCP.ALLOWED or not isinstance(arguments,dict): raise PlanGatewayError('tool unavailable')
        process=None
        try:
            if self.verify_policy() is not True: raise ValueError()
            deadline=time.monotonic()+self.TIMEOUT
            launcher=str(Path(__file__).with_name('plan_codex_launcher.py'))
            process=subprocess.Popen([sys.executable,'-I',launcher,str(os.getpid()),self.executable],cwd=self.workspace,env=self.environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,start_new_session=True)
            transport=ProcessTransport(process,deadline,lambda:False); server=AppServer(transport)
            server._request('initialize',{'clientInfo':{'name':'agent_workflows','version':'0.1.0'},'capabilities':{'experimentalApi':True}})
            transport.send({'method':'initialized','params':{}})
            thread=server._request('thread/start',{'ephemeral':True,'cwd':self.workspace})['thread']['id']
            if not isinstance(thread,str) or not thread: raise ValueError()
            result=server._request('mcpServer/tool/call',{'threadId':thread,'server':'codex_apps','tool':'it_s_a_plan_mcp.'+name,'arguments':arguments})
            data=result.get('structuredContent')
            if result.get('isError') is not False or not isinstance(data,dict) or data.get('ok') is not True or self.verify_policy() is not True: raise ValueError()
            return data
        except Exception: raise PlanGatewayError('tracker gateway unavailable') from None
        finally: _stop_gateway(process)
