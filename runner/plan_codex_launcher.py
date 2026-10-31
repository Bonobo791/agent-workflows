"""Terminate the gateway process group if its caller dies."""
import ctypes
import os
import signal
import subprocess
import sys

PR_SET_PDEATHSIG=1

def terminate_group(*_):
    os.killpg(os.getpgrp(),signal.SIGKILL)

def main():
    parent=int(sys.argv[1]); executable=sys.argv[2]
    if len(sys.argv)!=3 or not os.path.isabs(executable): raise ValueError()
    if os.getpgrp()!=os.getpid() or os.getsid(0)!=os.getpid(): raise ValueError()
    signal.signal(signal.SIGTERM,terminate_group)
    signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGTERM})
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG,signal.SIGTERM,0,0,0)!=0: raise OSError('parent-death signal unavailable')
    if os.getppid()!=parent: terminate_group()
    child=subprocess.Popen([executable,'app-server'])
    return child.wait()

if __name__=='__main__':
    try: sys.exit(main())
    except (OSError,ValueError,IndexError): sys.exit(1)
