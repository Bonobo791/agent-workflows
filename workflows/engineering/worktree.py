"""Create an isolated local task worktree from a verified main commit."""
import re
import subprocess
from pathlib import Path

class WorktreeError(RuntimeError):
    pass

def _git(repo, *args):
    try:
        return subprocess.run(['git','-c','core.hooksPath=/dev/null',*args], cwd=repo, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError): raise WorktreeError('git operation failed; inspect repository state') from None

def create_worktree(repo, workspace, branch, base_commit):
    if not isinstance(branch,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',branch): raise WorktreeError('invalid task branch')
    if not isinstance(base_commit,str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})',base_commit): raise WorktreeError('full commit ID required')
    repo=Path(repo).resolve(strict=True); root=Path(workspace).resolve(strict=True)
    target=root / branch
    if target.exists() or target.is_symlink(): raise WorktreeError('target already exists')
    if _git(repo,'rev-parse','--verify','refs/heads/main^{commit}') != base_commit: raise WorktreeError('base is not current local main')
    _git(repo,'worktree','add','-b',branch,str(target),base_commit)
    return target
