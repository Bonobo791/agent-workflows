"""Read GitHub through the owner's existing gh login, without exporting tokens."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time

class GHReadError(RuntimeError): pass

class GHRead:
    def __init__(self,executable,environment):
        try:
            path=Path(executable)
            if not path.is_absolute(): raise ValueError()
            self.executable=path.resolve(strict=True)
            self.environment={k:environment[k] for k in ('HOME','GH_CONFIG_DIR','XDG_CONFIG_HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if k in environment}
            if 'HOME' not in self.environment or not Path(self.environment['HOME']).is_absolute(): raise ValueError()
            self.environment['PATH']='/usr/bin:/bin';self.environment['GH_HOST']='github.com';self.environment['GH_PROMPT_DISABLED']='1'
            self.digest=self._digest()
        except Exception: raise GHReadError('GitHub CLI configuration unavailable') from None
    def _digest(self):
        fd=os.open(self.executable,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode&0o022 or not info.st_mode&0o111: raise ValueError()
            with os.fdopen(fd,'rb',closefd=False) as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
        finally:os.close(fd)
    def request(self,method,path,body=None):
        if method!='GET' or body is not None or not isinstance(path,str) or not path.startswith('/repos/') or any(c in path for c in '\r\n\0#{}') or len(path)>8192: raise GHReadError('unsupported GitHub read')
        process=None;selector=None;success=False
        try:
            if self._digest()!=self.digest: raise ValueError()
            process=subprocess.Popen([str(self.executable),'api','--hostname','github.com','--method','GET','--',path],env=self.environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,start_new_session=True)
            selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ,'stdout');selector.register(process.stderr,selectors.EVENT_READ,'stderr')
            values={'stdout':bytearray(),'stderr':bytearray()};deadline=time.monotonic()+30
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0: raise TimeoutError()
                for key,_ in selector.select(min(remaining,.1)):
                    data=os.read(key.fd,65536)
                    if not data:selector.unregister(key.fileobj);continue
                    values[key.data].extend(data)
                    if len(values[key.data])>(2097152 if key.data=='stdout' else 65536): raise ValueError()
            if process.wait(timeout=max(.01,deadline-time.monotonic()))!=0: raise ValueError()
            value=json.loads(values['stdout']);success=True;return value
        except Exception: raise GHReadError('GitHub read unavailable') from None
        finally:
            if selector is not None:selector.close()
            if process is not None:
                if not success:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except OSError:pass
                if process.poll() is None:
                    process.wait()
                for stream in (process.stdout,process.stderr):
                    if stream is not None:stream.close()
