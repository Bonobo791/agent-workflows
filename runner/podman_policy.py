"""Verify the local rootless renderer and its fixed helper before use."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import tempfile


class PodmanRenderPolicy:
    def __init__(self, config, environment):
        if config.engine!='podman-rootless' or config.network!='none': raise ValueError('rootless render policy required')
        self.config=config
        self.executable=Path('/usr/bin/podman')
        self.environment={k:environment[k] for k in ('HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if k in environment}
        self.environment['PATH']='/usr/bin:/bin'
        self.digest=self._digest()
        self.source_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        identity={'config':config.policy_hash,'executable':str(self.executable),'executable_sha256':self.digest,'verifier_sha256':self.source_digest,'environment':self.environment}
        self.policy_hash=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def _digest(self):
        fd=os.open(self.executable,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode&0o022: raise ValueError('unsafe Podman executable')
            with os.fdopen(fd,'rb',closefd=False) as file: return hashlib.file_digest(file,'sha256').hexdigest()
        finally: os.close(fd)

    def _endpoint(self):
        import grp
        import pwd
        info=os.lstat(self.config.socket)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o007: return False
        if 'system.posix_acl_access' in os.listxattr(self.config.socket): return False
        if info.st_mode&0o020:
            user=pwd.getpwuid(os.geteuid()).pw_name
            if set(grp.getgrgid(info.st_gid).gr_mem)-{user} or any(entry.pw_gid==info.st_gid and entry.pw_uid!=os.geteuid() for entry in pwd.getpwall()): return False
        for parent in Path(self.config.socket).parents:
            details=parent.lstat()
            if not stat.S_ISDIR(details.st_mode) or details.st_mode&0o022: return False
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
            peer.settimeout(5);peer.connect(self.config.socket)
            _,uid,_=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        after=os.lstat(self.config.socket)
        return uid==os.geteuid() and (info.st_dev,info.st_ino)==(after.st_dev,after.st_ino)

    def _run(self,*args):
        command=[str(self.executable),'--url','unix://'+self.config.socket,*args]
        result=subprocess.run(command,env=self.environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=30,check=True)
        if len(result.stdout)>65536: raise ValueError('Podman response too large')
        return result.stdout.decode('utf-8').strip()

    def __call__(self,policy_hash):
        cid=None; verified=False
        try:
            if not isinstance(policy_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',policy_hash) or self._digest()!=self.digest or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=self.source_digest or not self._endpoint(): return False
            security=json.loads(self._run('info','--format','{{json .Host.Security}}'))
            if any(security.get(key) is not True for key in ('rootless','seccompEnabled','selinuxEnabled')): return False
            image=self._run('image','inspect',self.config.image,'--format','{{.Id}}')
            if 'sha256:'+image!=self.config.image: return False
            cid=self._run('create','--quiet','--network','none','--read-only','--cap-drop','ALL','--entrypoint','/bin/false','--user',f'{os.geteuid()}:{os.getegid()}','--label','agent-workflows.policy-probe=1',self.config.image)
            if not re.fullmatch(r'[0-9a-f]{64}',cid): cid=None; return False
            with tempfile.TemporaryDirectory(prefix='aw-render-policy-') as directory:
                helper=Path(directory)/'helper.mjs'
                self._run('cp',cid+':/app/runner/openfilm_render.mjs',str(helper))
                info=helper.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_size>1048576: return False
                expected=Path(__file__).with_name('openfilm_render.mjs').read_bytes()
                verified=hashlib.sha256(helper.read_bytes()).digest()==hashlib.sha256(expected).digest()
        except (OSError,ValueError,TypeError,KeyError,UnicodeError,subprocess.SubprocessError): return False
        finally:
            if cid is not None:
                try:
                    if self._run('rm',cid)!=cid: verified=False
                except (OSError,ValueError,UnicodeError,subprocess.SubprocessError): verified=False
        return verified
