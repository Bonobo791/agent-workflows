import tempfile
import unittest
from pathlib import Path
from runner.docker_config import DockerConfig, ConfigError

IMAGE='worker@sha256:'+'1'*64
class ConfigTests(unittest.TestCase):
    def test_hash_binds_complete_config_and_exact_target(self):
        with tempfile.TemporaryDirectory() as root:
            config={'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(Path(root)/'jobs')}
            Path(config['job_root']).mkdir(mode=0o700)
            first=DockerConfig(config,root)
            second=DockerConfig({**config,'socket':'/private/other.sock'},root)
            self.assertNotEqual(first.policy_hash,second.policy_hash)
            self.assertEqual(first.job_root,Path(root)/'jobs')
    def test_rejects_escape_and_unapproved_properties(self):
        with tempfile.TemporaryDirectory() as root:
            job_root=Path(root)/'jobs'; job_root.mkdir(mode=0o700)
            config={'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(job_root)}
            for change in ({'job_root':'/tmp'}, {'network':'host'}, {'network':'bridge'}, {'privileged':True}, {'cap_add':['SYS_ADMIN']}, {'socket':str(job_root/'engine.sock')}, {'image':'latest'}):
                with self.subTest(change=change), self.assertRaises(ConfigError): DockerConfig({**config,**change},root)
    def test_rejects_symlinked_job_root(self):
        with tempfile.TemporaryDirectory() as root:
            real=Path(root)/'real'; real.mkdir(mode=0o700); link=Path(root)/'jobs'; link.symlink_to(real)
            with self.assertRaises(ConfigError): DockerConfig({'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(link)},root)
    def test_job_mounts_are_generated_inside_private_root(self):
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(jobs)},root)
            mounts=config.create_job_dirs('job-1')
            self.assertEqual(Path(mounts['workspace']),jobs/'job-1'/'workspace')
            self.assertEqual(Path(mounts['output']),jobs/'job-1'/'output')
            for bad in ('../outside','/tmp/escape','job:mount'):
                with self.assertRaises(ConfigError): config.create_job_dirs(bad)
            with self.assertRaises(ConfigError): config.create_job_dirs('job-1')
    def test_policy_cannot_be_mutated_after_hashing(self):
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(jobs)},root)
            with self.assertRaises(AttributeError): config.network='host'
