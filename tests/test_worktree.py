import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from workflows.engineering.worktree import create_worktree, WorktreeError

def git(cwd,*args):
    return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True,text=True).stdout.strip()

class WorktreeTests(unittest.TestCase):
    def test_creates_isolated_branch_and_rejects_unsafe_names(self):
        with tempfile.TemporaryDirectory() as root:
            repo=Path(root)/'repo'; repo.mkdir(); git(repo,'init','-b','main'); git(repo,'config','user.email','test@example.invalid'); git(repo,'config','user.name','Test')
            (repo/'file').write_text('initial'); git(repo,'add','file'); git(repo,'commit','-m','initial')
            base=git(repo,'rev-parse','HEAD'); workspace=Path(root)/'worktrees'; workspace.mkdir()
            with self.assertRaises(WorktreeError): create_worktree(repo,workspace,'../bad',base)
            path=create_worktree(repo,workspace,'task-42',base)
            self.assertEqual(git(path,'rev-parse','HEAD'),base)
            self.assertEqual(git(path,'branch','--show-current'),'task-42')
            self.assertEqual(git(repo,'branch','--show-current'),'main')
