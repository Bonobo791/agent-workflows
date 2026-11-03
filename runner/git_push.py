"""Host-side git push argument construction and fast-forward verification."""
import re
import subprocess
from workflows.engineering.github import GitHub

class GitPushError(RuntimeError): pass

def push_argv(repo,branch,head,expected_remote):
    if not isinstance(repo,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo): raise GitPushError('invalid repository')
    GitHub._branch(branch); GitHub._sha(head)
    if expected_remote is not None: GitHub._sha(expected_remote)
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',head) or (expected_remote is not None and not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',expected_remote)): raise GitPushError('full object IDs required')
    return ['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-c','credential.helper=','push','--porcelain','--force-with-lease=refs/heads/'+branch+':'+(expected_remote or ''),'--','https://github.com/'+repo+'.git',head+':refs/heads/'+branch]

def require_fast_forward(path,expected_remote,head):
    if expected_remote is None: return
    try:
        result=subprocess.run(['git','-C',path,'merge-base','--is-ancestor',expected_remote,head],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_NO_REPLACE_OBJECTS':'1'})
    except (OSError,subprocess.TimeoutExpired): raise GitPushError('ancestry verification unavailable')
    if result.returncode!=0: raise GitPushError('approved push is not a verified fast-forward')
