import json
import os
import tempfile
import unittest
import stat
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from runner.docker_config import DockerConfig
from runner.podman_policy import PodmanRenderPolicy

class PodmanPolicyTests(unittest.TestCase):
    def test_policy_identity_changes_with_podman_executable(self):
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs';jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/tmp/test.sock','image':'sha256:'+'1'*64,'network':'none','job_root':str(jobs),'engine':'podman-rootless'},root)
            with patch.object(PodmanRenderPolicy,'_digest',side_effect=['a'*64,'b'*64]):
                first=PodmanRenderPolicy(config,{'HOME':root}); second=PodmanRenderPolicy(config,{'HOME':root})
            self.assertNotEqual(first.policy_hash,second.policy_hash)
            from runner.docker_openfilm import DockerOpenFilmBackend
            from runner.openfilm_builder import CodexOpenFilmProjectBuilder
            render=object.__new__(DockerOpenFilmBackend);render.supervisor=SimpleNamespace(config=config);render.timeout_seconds=60;render.verify_policy=first
            builder=object.__new__(CodexOpenFilmProjectBuilder);builder.supervisor=SimpleNamespace(policy_hash='c'*64);builder.local=True;builder.timeout_seconds=60;builder.verify_policy=first
            render_hash=render._policy();builder_hash=builder._policy()
            render.verify_policy=second;builder.verify_policy=second
            self.assertNotEqual(render_hash,render._policy());self.assertNotEqual(builder_hash,builder._policy())
    def test_checks_image_helper_and_removes_exact_probe(self):
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs';jobs.mkdir(mode=0o700)
            image='sha256:'+'1'*64
            config=DockerConfig({'socket':'/tmp/test.sock','image':image,'network':'none','job_root':str(jobs),'engine':'podman-rootless'},root)
            with patch.object(PodmanRenderPolicy,'_digest',return_value='pinned'):
                policy=PodmanRenderPolicy(config,{'HOME':root,'API_KEY':'never-forward'})
            self.assertNotIn('API_KEY',policy.environment)
            cid='a'*64;calls=[]
            def run(*args):
                calls.append(args)
                if args[0]=='info': return json.dumps({'rootless':True,'seccompEnabled':True,'selinuxEnabled':True})
                if args[:2]==('image','inspect'): return '1'*64
                if args[0]=='create': return cid
                if args[0]=='cp': Path(args[2]).write_bytes(Path('runner/openfilm_render.mjs').read_bytes());return ''
                if args[0]=='rm': return cid
                raise AssertionError(args)
            with patch.object(policy,'_digest',return_value='pinned'),patch.object(policy,'_endpoint',return_value=True),patch.object(policy,'_run',side_effect=run):
                self.assertTrue(policy('f'*64))
                self.assertEqual(calls[-1],('rm',cid))
                self.assertFalse(policy('invalid'))
                def bad(*args):
                    if args[0]=='cp': Path(args[2]).write_bytes(b'wrong');return ''
                    return run(*args)
                with patch.object(policy,'_run',side_effect=bad): self.assertFalse(policy('f'*64))
                self.assertEqual(calls[-1],('rm',cid))
                def bad_cleanup(*args):
                    return '' if args[0]=='rm' else run(*args)
                with patch.object(policy,'_run',side_effect=bad_cleanup): self.assertFalse(policy('f'*64))
    def test_rejects_non_rootless_engine_policy(self):
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs';jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/tmp/test.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)},root)
            with self.assertRaises(ValueError): PodmanRenderPolicy(config,{'HOME':root})
    def test_socket_shared_with_another_account_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs';jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/tmp/test.sock','image':'sha256:'+'1'*64,'network':'none','job_root':str(jobs),'engine':'podman-rootless'},root)
            with patch.object(PodmanRenderPolicy,'_digest',return_value='pinned'):
                policy=PodmanRenderPolicy(config,{'HOME':root})
            info=SimpleNamespace(st_mode=stat.S_IFSOCK|0o660,st_uid=os.geteuid(),st_gid=42)
            with patch('runner.podman_policy.os.lstat',return_value=info),patch('runner.podman_policy.os.listxattr',return_value=[]),patch('pwd.getpwuid',return_value=SimpleNamespace(pw_name='owner')),patch('grp.getgrgid',return_value=SimpleNamespace(gr_mem=['someone-else'])):
                self.assertFalse(policy._endpoint())
