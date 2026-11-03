"""Start a local command inside a named systemd user scope."""
import subprocess
import threading
import time


class LaunchError(RuntimeError): pass


def reap_after_failure(process):
    def wait():
        try:process.wait()
        except OSError:pass
    try:process.wait(timeout=1)
    except subprocess.TimeoutExpired:threading.Thread(target=wait,daemon=True).start()
    except OSError:pass


def start_scope(unit,argv,*,cwd,env,backend,deadline):
    """Return once systemd reports UNIT active; the caller binds it next."""
    # systemd-run registers the unit asynchronously, so the poll is what makes bind safe.
    process=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--',*argv],
                             cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
    try:
        while backend.inspect(unit).get('active')!='active':
            if process.poll() is not None or time.monotonic()>=deadline: raise LaunchError('scope did not start')
            time.sleep(.05)
        return process
    except BaseException:
        for stream in (process.stdin,process.stdout):
            if stream is not None:stream.close()
        reap_after_failure(process)
        raise
