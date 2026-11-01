"""Private handshake for a scoped process that must not start before binding."""
import json
import math
import os
from pathlib import Path
import secrets
import select
import subprocess
import threading
import time


class LaunchError(RuntimeError): pass


def reap_after_failure(process):
    def wait():
        try:process.wait()
        except (OSError,ChildProcessError):pass
    try:process.wait(timeout=1)
    except subprocess.TimeoutExpired:threading.Thread(target=wait,daemon=True).start()
    except (OSError,ChildProcessError):pass


class GuardedLaunch:
    def __init__(self,process,ack_fd,go_fd,nonce):
        self.process,self.ack_fd,self.go_fd,self.nonce=process,ack_fd,go_fd,nonce
        self.receipt=None

    def receive(self,deadline,cancelled):
        if self.ack_fd is None or not callable(cancelled): raise LaunchError('launch receipt unavailable')
        data=b''
        try:
            while True:
                if cancelled() or time.monotonic()>=deadline: raise LaunchError('launch receipt unavailable')
                ready,_,_=select.select([self.ack_fd],[],[],min(.1,deadline-time.monotonic()))
                if not ready: continue
                chunk=os.read(self.ack_fd,513-len(data))
                if not chunk: break
                data+=chunk
                if len(data)>512: raise LaunchError('launch receipt unavailable')
            receipt=json.loads(data)
            if (not isinstance(receipt,dict) or set(receipt)!={'version','nonce','pid'}
                    or type(receipt['version']) is not int or receipt['version']!=1
                    or receipt['nonce']!=self.nonce or type(receipt['pid']) is not int
                    or receipt['pid']!=self.process.pid or self.process.poll() is not None):
                raise LaunchError('launch receipt unavailable')
            if cancelled() or time.monotonic()>=deadline: raise LaunchError('launch receipt unavailable')
            self.receipt=(receipt['pid'],receipt['nonce'])
            return receipt
        except (OSError,ValueError,TypeError):
            raise LaunchError('launch receipt unavailable') from None
        finally:
            os.close(self.ack_fd);self.ack_fd=None

    def release(self):
        if self.receipt!=(self.process.pid,self.nonce) or self.go_fd is None or self.process.poll() is not None:
            raise LaunchError('launch authorization unavailable')
        try:
            if os.write(self.go_fd,b'G')!=1: raise LaunchError('launch authorization unavailable')
        except OSError:
            raise LaunchError('launch authorization unavailable') from None
        finally:
            os.close(self.go_fd);self.go_fd=None

    def close(self):
        for name in ('ack_fd','go_fd'):
            fd=getattr(self,name)
            if fd is not None:
                os.close(fd);setattr(self,name,None)


def start_guarded(prefix,target,*,cwd,env,deadline=None):
    if not isinstance(prefix,list) or not isinstance(target,list) or not target or any(not isinstance(x,str) or '\x00' in x for x in prefix+target) or not os.path.isabs(target[0]):
        raise LaunchError('invalid launch command')
    if deadline is None:deadline=time.monotonic()+60
    if type(deadline) not in (int,float) or not math.isfinite(deadline) or deadline<=time.monotonic(): raise LaunchError('launch deadline unavailable')
    ack_read,ack_write=os.pipe2(os.O_CLOEXEC)
    try:
        go_read,go_write=os.pipe2(os.O_CLOEXEC)
    except BaseException:
        os.close(ack_read);os.close(ack_write);raise
    try:
        nonce=secrets.token_hex(32)
        guard=str(Path(__file__).with_name('local_launch_guard.py').resolve(strict=True))
        command=prefix+['/usr/bin/python3','-I',guard,str(ack_write),str(go_read),nonce,str(deadline)]+target
        process=subprocess.Popen(command,cwd=cwd,env=env,pass_fds=(ack_write,go_read),
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
    except BaseException:
        for fd in (ack_read,ack_write,go_read,go_write):os.close(fd)
        raise
    os.close(ack_write);os.close(go_read)
    return GuardedLaunch(process,ack_read,go_write,nonce)
