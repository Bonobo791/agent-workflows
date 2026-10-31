"""Inspect the local Codex launch inputs without exposing configuration values."""
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys
import time

from runner.adapter import managed_app_server_spec
from runner.process_transport import ProcessTransport
from runner.protocol import AppServer


class InspectionError(RuntimeError): pass


def _native_executable(executable):
    path=Path(executable)
    if not path.is_absolute(): raise ValueError('absolute executable required')
    path=path.resolve(strict=True)
    package=path.parent.parent
    if sys.platform!='linux' or path.name!='codex.js' or path.parent.name!='bin' or package.name!='codex' or package.parent.name!='@openai': return str(path)
    if json.loads((package/'package.json').read_text())['name']!='@openai/codex': raise ValueError('invalid Codex package')
    target={'x86_64':('codex-linux-x64','x86_64-unknown-linux-musl'),'aarch64':('codex-linux-arm64','aarch64-unknown-linux-musl')}.get(platform.machine())
    if target is None: raise ValueError('unsupported Codex package architecture')
    package_name,triple=target
    roots=(package/'node_modules'/'@openai'/package_name/'vendor',package.parent.parent/'@openai'/package_name/'vendor',package/'vendor')
    for root in roots:
        candidate=root/triple/'bin'/'codex'
        if candidate.is_file(): return str(candidate.resolve(strict=True))
    raise ValueError('native Codex executable unavailable')


def _binary_hash(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o022 or not info.st_mode&0o111: raise ValueError()
        with os.fdopen(fd,'rb',closefd=False) as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    finally:os.close(fd)


def inspect_config(executable,workspace,environment):
    process=None
    try:
        path=Path(workspace);info=path.lstat()
        if not path.is_absolute() or path.resolve(strict=True)!=path or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077: raise ValueError()
        args,env=managed_app_server_spec(_native_executable(executable),environment,disabled_integrations={'mcp_servers':[],'plugins':[]})
        binary=Path(args[0]);digest=_binary_hash(binary)
        process=subprocess.Popen(args,cwd=str(path),env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,start_new_session=True)
        server=AppServer(ProcessTransport(process,time.monotonic()+30,lambda:False));server.initialize()
        config=server._request('config/read',{'cwd':str(path),'includeLayers':False})['config']
        if not isinstance(config,dict): raise ValueError()
        integrations={}
        for kind in ('mcp_servers','plugins'):
            values=config.get(kind,{})
            if not isinstance(values,dict) or len(values)>256 or any(not isinstance(name,str) or not 1<=len(name)<=256 or any(ord(c)<32 or ord(c)==127 for c in name) for name in values): raise ValueError()
            integrations[kind]=sorted(values)
        if _binary_hash(binary)!=digest: raise ValueError()
        return {'codex_executable':str(binary),'codex_sha256':digest,'disabled_integrations':integrations}
    except Exception: raise InspectionError('local runtime inspection unavailable') from None
    finally:
        if process is not None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait(timeout=5)
            for stream in (process.stdin,process.stdout):
                if stream is not None:stream.close()


def main():
    if len(sys.argv)!=3: raise InspectionError('expected an absolute Codex executable and private workspace')
    print(json.dumps(inspect_config(sys.argv[1],sys.argv[2],os.environ),sort_keys=True,indent=2))


if __name__=='__main__':
    try:main()
    except InspectionError:
        print('Local runtime inspection failed.',file=sys.stderr);sys.exit(1)
