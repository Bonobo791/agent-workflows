"""Import an exact commit into an isolated, disposable bare Git repository."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from runner.local_command import run_scoped_sandbox

class SnapshotError(RuntimeError): pass

@contextmanager
def scoped_git_snapshot(supervisor,run,workspace,head,on_reserved,*,on_started=None,workspace_claim=None):
    if not isinstance(head,str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',head) or not callable(on_reserved): raise SnapshotError('exact commit and scope recorder required')
    try:
        root=Path(supervisor.db).parent;info=root.lstat();work=Path(workspace);winfo=work.lstat()
        if not root.is_absolute() or root.resolve(strict=True)!=root or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077: raise ValueError()
        if not work.is_absolute() or work.resolve(strict=True)!=work or not stat.S_ISDIR(winfo.st_mode) or winfo.st_uid!=os.geteuid() or winfo.st_mode&0o077: raise ValueError()
        if supervisor.preflight() is not True or supervisor.fence(run) is not True: raise ValueError()
    except Exception: raise SnapshotError('scoped Git snapshot unavailable') from None
    with tempfile.TemporaryDirectory(prefix='git-bundle-',dir=root) as output:
        bundle=str(Path(output)/'checkout.bundle')
        args=['/usr/bin/git','--no-replace-objects','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-c','credential.helper=','bundle','create',bundle,'HEAD']
        try:
            result=run_scoped_sandbox(supervisor,run,workspace,args,time.monotonic()+60,8192,on_reserved,output_dir=output,on_started=on_started,**({'workspace_claim':workspace_claim} if workspace_claim is not None else {}))
            if result.get('outcome')!='exited' or result.get('exit_code')!=0 or not isinstance(result.get('unit'),str): raise ValueError()
        except Exception: raise SnapshotError('scoped Git bundle unavailable') from None
        with isolated_git_snapshot(bundle,head,str(root)) as bare:
            yield bare,result['unit']

@contextmanager
def isolated_git_snapshot(bundle,head,state_root):
    if not isinstance(head,str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',head): raise SnapshotError('full commit required')
    fd=None
    try:
        root=Path(state_root);info=root.lstat()
        if not root.is_absolute() or root.resolve(strict=True)!=root or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077: raise ValueError()
        fd=os.open(bundle,os.O_RDONLY|os.O_NOFOLLOW)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_size>512*1024*1024: raise ValueError()
        size=info.st_size
    except (OSError,ValueError,TypeError):
        if fd is not None:os.close(fd)
        raise SnapshotError('private bundle or state root unavailable') from None
    try:
        with tempfile.TemporaryDirectory(prefix='git-snapshot-',dir=root) as directory:
            temp=Path(directory); local_bundle=temp/'input.bundle';template=temp/'template';template.mkdir(mode=0o700);bare=temp/'repo.git'
            try:
                with os.fdopen(fd,'rb',closefd=False) as source,open(local_bundle,'xb') as dest:
                    remaining=size
                    while remaining:
                        chunk=source.read(min(remaining,1024*1024))
                        if not chunk: raise ValueError()
                        dest.write(chunk);remaining-=len(chunk)
                    if source.read(1): raise ValueError()
                env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','GIT_NO_REPLACE_OBJECTS':'1','LC_ALL':'C'}
                def git(*args,capture=False):
                    result=subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-c','gc.auto=0',*args],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE if capture else subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=True)
                    return result.stdout.decode('ascii').strip() if capture else None
                git('init','--bare','--quiet','--template='+str(template),'--object-format='+('sha1' if len(head)==40 else 'sha256'),str(bare))
                git('--git-dir='+str(bare),'fetch','--quiet','--no-tags','--no-write-fetch-head','--no-recurse-submodules','--',str(local_bundle),'HEAD:refs/heads/snapshot')
                if git('--git-dir='+str(bare),'rev-parse','--verify','refs/heads/snapshot^{commit}',capture=True)!=head: raise ValueError()
                git('--git-dir='+str(bare),'fsck','--full','--strict','--no-reflogs')
            except (OSError,ValueError,UnicodeError,subprocess.SubprocessError): raise SnapshotError('bundle import or exact commit verification failed') from None
            yield str(bare)
    finally:os.close(fd)
