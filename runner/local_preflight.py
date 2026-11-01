"""Synthetic, local checks for the managed Codex command sandbox."""
import subprocess
import tempfile
from pathlib import Path
from runner.adapter import managed_app_server_spec

_PROBE='''import errno, pathlib, socket, sys
private=pathlib.Path(sys.argv[1])
try:
    private.read_bytes()
    denied=False
except (FileNotFoundError, PermissionError): denied=True
print('private_denied='+str(int(denied)))
try:
    pathlib.Path('probe-output').write_text('synthetic')
    print('workspace_write=1')
except OSError: print('workspace_write=0')
try:
    s=socket.socket(); s.settimeout(1); s.connect(('1.1.1.1',443))
    print('network_denied=0')
except OSError as e: print('network_denied='+str(int(e.errno in (errno.EPERM,errno.EACCES))))
'''
_EXPECTED='private_denied=1\nworkspace_write=1\nnetwork_denied=1\n'

def sandbox_preflight(executable,environment):
    try:
        app,env=managed_app_server_spec(executable,environment)
        with tempfile.TemporaryDirectory(prefix='agent-workflows-preflight-') as root:
            root=Path(root); workspace=root/'workspace'; workspace.mkdir(mode=0o700)
            private=root/'private'; private.mkdir(mode=0o700); canary=private/'canary'; canary.write_text('synthetic')
            args=[app[0],'sandbox','-C',str(workspace),'-P','workflow_local',*app[4:],'--','/usr/bin/python3','-c',_PROBE,str(canary)]
            result=subprocess.run(args,cwd=str(workspace),env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=20,check=False)
            return result.returncode==0 and result.stdout==_EXPECTED and (workspace/'probe-output').read_text()=='synthetic'
    except (OSError,ValueError,TypeError,subprocess.SubprocessError,RuntimeError): return False

def _scope_absent(backend,unit):
    """True only when systemd itself reports the reserved unit never existed."""
    try: info=backend.inspect(unit)
    except Exception: return False
    return info.get('id')==unit and info.get('load')=='not-found' and info.get('active')=='inactive' and info.get('invocation')==''


def scope_preflight(environment):
    """Check a real user scope kills a synthetic detached child."""
    import os, select, sys
    from runner.local_scope import LocalScopes, SystemdScopes
    manager={k:environment[k] for k in ('HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if k in environment}
    manager['PATH']='/usr/bin:/bin'
    backend=SystemdScopes(manager)
    process=None; scopes=None; unit=None; root=None; clean=False; started=False; exited=False
    try:
        root=tempfile.mkdtemp(prefix='agent-workflows-scope-preflight-')
        scopes=LocalScopes(str(Path(root)/'state.db'),backend);unit=scopes.reserve('preflight')
        code='import subprocess,time; p=subprocess.Popen(["/usr/bin/sleep","60"],start_new_session=True); print(p.pid,flush=True); time.sleep(60)'
        process=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--','/usr/bin/python3','-c',code],env=manager,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        ready,_,_=select.select([process.stdout],[],[],10)
        if not ready: return False
        line=process.stdout.readline().strip()
        if not line.isascii() or not line.isdigit(): return False
        child=int(line)
        if child<=1: return False
        started=True
        scopes.bind('preflight',unit)
        info=backend.inspect(unit)
        membership=Path('/proc')/str(child)/'cgroup'
        if '0::'+info['cgroup'] not in membership.read_text().splitlines(): return False
        if scopes.stop('preflight',unit) is not True: return False
        process.wait(timeout=10)
        try: os.kill(child,0); return False
        except ProcessLookupError:
            clean=True
            return True
    except (OSError,ValueError,TypeError,subprocess.SubprocessError,RuntimeError): return False
    finally:
        if scopes is not None and unit is not None:
            try:
                if scopes.state('preflight',unit)=='running': scopes.stop('preflight',unit)
            except Exception: pass
        exited=process is not None and process.poll() is not None and process.returncode not in (None,0)
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait()
        if scopes is not None: scopes.close()
        if process is not None and process.stdout is not None: process.stdout.close()
        if root is not None:
            if clean or process is None or (exited and not started and _scope_absent(backend,unit)):
                import shutil
                shutil.rmtree(root)
            else:
                print('Scope preflight requires inspection: '+root,file=sys.stderr)
