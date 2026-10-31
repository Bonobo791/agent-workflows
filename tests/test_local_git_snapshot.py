import subprocess
import tempfile
import unittest
from pathlib import Path
from runner.local_git_snapshot import isolated_git_snapshot, scoped_git_snapshot, SnapshotError

def git(path,*args):
    return subprocess.run(['/usr/bin/git','-C',str(path),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True).stdout.strip()

class LocalGitSnapshotTests(unittest.TestCase):
    def test_bundle_is_imported_to_clean_bare_repository_at_exact_commit(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);source=root/'source';source.mkdir(mode=0o700);git(source,'init','-q');(source/'file').write_text('synthetic');git(source,'add','file');git(source,'-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qm','fixture')
            head=git(source,'rev-parse','HEAD');bundle=root/'fixture.bundle';git(source,'bundle','create',str(bundle),'HEAD')
            with isolated_git_snapshot(str(bundle),head,str(root)) as bare:
                self.assertEqual(git(bare,'rev-parse','refs/heads/snapshot'),head)
                self.assertFalse((Path(bare)/'hooks'/'pre-push').exists())
                location=Path(bare)
            self.assertFalse(location.exists())
            with self.assertRaises(SnapshotError):
                with isolated_git_snapshot(str(bundle),'0'*40,str(root)):pass
    def test_rejects_symlink_bundle_and_unsafe_parent(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);target=root/'target';target.write_text('x');link=root/'link';link.symlink_to(target)
            with self.assertRaises(SnapshotError):
                with isolated_git_snapshot(str(link),'0'*40,str(root)):pass
    def test_scoped_bundle_is_verified_before_use(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);source=root/'source';source.mkdir(mode=0o700);git(source,'init','-q');(source/'file').write_text('synthetic');git(source,'add','file');git(source,'-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qm','fixture')
            head=git(source,'rev-parse','HEAD');supervisor=SimpleNamespace(db=str(root/'state.db'),preflight=lambda:True,fence=lambda run:True)
            observed=[];claims=[]
            def run(s,run,workspace,argv,deadline,limit,on_reserved,*,output_dir,on_started=None,workspace_claim=None):
                claims.append(workspace_claim)
                on_reserved('scope');git(source,'bundle','create',str(Path(output_dir)/'checkout.bundle'),'HEAD')
                return {'outcome':'exited','exit_code':0,'stdout':b'','stderr':b'','unit':'scope'}
            with patch('runner.local_git_snapshot.run_scoped_sandbox',side_effect=run):
                with scoped_git_snapshot(supervisor,'run',str(source),head,observed.append) as (bare,unit):
                    self.assertEqual(unit,'scope');self.assertEqual(git(bare,'rev-parse','refs/heads/snapshot'),head)
                self.assertEqual(observed,['scope'])
                binding=(str(root),'job-'+'a'*48,'b'*64)
                with scoped_git_snapshot(supervisor,'run',str(source),head,lambda _:None,workspace_claim=binding): pass
                self.assertEqual(claims,[None,binding])
                with self.assertRaises(SnapshotError):
                    with scoped_git_snapshot(supervisor,'run',str(source),'0'*40,lambda _:None):pass
