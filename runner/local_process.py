"""A scoped local Codex process; workflow admission and recovery are separate."""
import os
from pathlib import Path
import stat
import subprocess
import time
from runner.adapter import managed_app_server_spec, verify_managed_workspace
from runner.local_scope import LocalScopes, reserve_for_execution
from runner.local_launch import start_guarded
from runner.process_transport import ProcessTransport

class LocalProcessError(RuntimeError): pass

class LocalProcessHandle:
    def __init__(self,db,run,unit,workspace,output_dir,executable,process,backend,fence,git_dir=None):
        self.db,self.run,self.unit,self.workspace,self.output_dir=db,run,unit,workspace,output_dir
        self.output_path_prefix=output_dir
        self.executable,self.process,self.backend,self.fence=executable,process,backend,fence
        self.git_dir=git_dir
    def assert_active(self):
        if self.fence() is not True or self.process.poll() is not None: raise LocalProcessError('local process or run fence unavailable')
        scopes=LocalScopes(self.db,self.backend)
        try: return scopes.assert_active(self.run,self.unit)
        finally: scopes.close()
    def open_transport(self,deadline,cancelled):
        self.assert_active()
        return ProcessTransport(self.process,deadline,cancelled)
    def verify_config(self,request):
        self.assert_active()
        return verify_managed_workspace(request,self.workspace,self.executable,output_dir=self.output_dir,git_dir=self.git_dir)
    def finish(self):
        scopes=LocalScopes(self.db,self.backend)
        try:
            if scopes.stop(self.run,self.unit) is not True: raise LocalProcessError('scope termination unverified')
        finally: scopes.close()
        try: self.process.wait(timeout=10)
        except subprocess.TimeoutExpired: raise LocalProcessError('scope client termination uncertain') from None
        for stream in (self.process.stdin,self.process.stdout):
            if stream is not None: stream.close()
        return True
    def cancel(self): return self.finish()

def start_local_process(db,run,workspace,executable,environment,backend,fence,disabled_integrations,*,allow_git=False,workspace_claim=None):
    if not callable(fence) or fence() is not True: raise LocalProcessError('active run fence required')
    if type(allow_git) is not bool: raise LocalProcessError('invalid Git policy')
    try:
        path=Path(workspace)
        info=path.lstat()
        if not path.is_absolute() or any(ord(c)<32 for c in str(path)) or path.resolve(strict=True)!=path or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077: raise ValueError()
        args,child_env=managed_app_server_spec(executable,environment,disabled_integrations=disabled_integrations)
    except (OSError,TypeError,ValueError): raise LocalProcessError('private canonical workspace required') from None
    manager_env={k:environment[k] for k in ('HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if k in environment}
    manager_env['PATH']='/usr/bin:/bin'
    unit=reserve_for_execution(db,run,backend,path,workspace_claim)
    try:
        state=Path(db).parent
        info=state.lstat()
        if not state.is_absolute() or state.resolve(strict=True)!=state or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077: raise ValueError()
        output_root=state/'local-outputs'
        output_root.mkdir(mode=0o700,exist_ok=True)
        info=output_root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or output_root.resolve(strict=True)!=output_root: raise ValueError()
        output_dir=output_root/unit
        output_dir.mkdir(mode=0o700)
        git_dir=str(path/'.git') if allow_git else None
        args,child_env=managed_app_server_spec(executable,environment,disabled_integrations=disabled_integrations,output_dir=str(output_dir),git_dir=git_dir)
    except (OSError,ValueError): raise LocalProcessError('private output allocation unavailable') from None
    command=['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--','/usr/bin/env','-i']
    command.extend(k+'='+v for k,v in child_env.items())
    launch=None
    try:
        launch=start_guarded(command,args,cwd=str(path),env=manager_env)
        launch.receive(time.monotonic()+10,lambda:fence() is not True)
        scopes=LocalScopes(db,backend)
        try:
            scopes.bind_confirmed(run,unit,launch)
            scopes.release_confirmed(run,unit,launch,fence)
        finally: scopes.close()
        return LocalProcessHandle(db,run,unit,str(path),str(output_dir),args[0],launch.process,backend,fence,git_dir)
    except Exception:
        if launch is not None:
            launch.close()
            for stream in (launch.process.stdin,launch.process.stdout):
                if stream is not None:stream.close()
            try:launch.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                launch.process.kill();launch.process.wait(timeout=5)
        raise LocalProcessError('scope launch outcome unresolved') from None
