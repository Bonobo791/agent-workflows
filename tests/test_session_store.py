import tempfile
import unittest
from pathlib import Path
from runner.session_store import SessionStore, SessionError

CONFIG='a'*64
SKILLS='b'*64
class SessionStoreTests(unittest.TestCase):
    def test_writer_replacement_reuses_only_matching_task_role_and_binding(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root)
            first=store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            self.assertTrue(Path(first['home']).is_dir())
            store.bind('job-1','thread-1')
            with self.assertRaises(SessionError): store.acquire('run-1','implement','job-2',CONFIG,SKILLS)
            with self.assertRaises(SessionError): store.release('job-1',lambda _:False)
            store.release('job-1',lambda _:True)
            with self.assertRaises(SessionError): store.acquire('run-1','implement','job-2',CONFIG,SKILLS,session='thread-1')
            second=store.acquire('run-1','implement','job-2',CONFIG,SKILLS,session='thread-1',verify_safe=lambda _:True)
            self.assertEqual(second['home'],first['home'])
            with self.assertRaises(SessionError): store.acquire('run-2','implement','job-3',CONFIG,SKILLS,session='thread-1')
            store.release('job-2',lambda _:True)
            with self.assertRaises(SessionError): store.acquire('run-1','implement','job-4',CONFIG,'c'*64,session='thread-1',verify_safe=lambda _:True)
            with self.assertRaises(SessionError): store.acquire('run-1','implement','job-5','d'*64,SKILLS,session='thread-1',verify_safe=lambda _:True)
            store.close()
    def test_reviewers_get_fresh_homes_and_cannot_resume(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root)
            first=store.acquire('run-1','qa','job-1',CONFIG,SKILLS)
            store.bind('job-1','review-1'); store.release('job-1',lambda _:True)
            second=store.acquire('run-1','qa','job-2',CONFIG,SKILLS)
            self.assertNotEqual(first['home'],second['home'])
            store.release('job-2',lambda _:True)
            with self.assertRaises(SessionError): store.acquire('run-1','qa','job-3',CONFIG,SKILLS,session='review-1')
            store.close()
    def test_escape_credentials_and_unverified_export_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root)
            with self.assertRaises(SessionError): store.acquire('../escape','implement','job-1',CONFIG,SKILLS)
            entry=store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            Path(entry['home'],'auth.json').write_text('synthetic secret')
            with self.assertRaises(SessionError): store.release('job-1',lambda _:True)
            with self.assertRaises(SessionError): store.backup('job-1',lambda *_:True)
            store.close()
    def test_backup_requires_verified_credential_boundary_and_idle_home(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root); entry=store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            Path(entry['home'],'synthetic-state').write_text('synthetic')
            store.bind('job-1','thread-1'); store.release('job-1',lambda _:True)
            with self.assertRaises(SessionError): store.backup('job-1',lambda *_:'backup',lambda _:False)
            self.assertEqual(store.backup('job-1',lambda path:'backup',lambda _:True),'backup')
            store.close()
    def test_interrupted_backup_remains_fenced_until_verified_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root); store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            store.bind('job-1','thread-1'); store.release('job-1',lambda _:True)
            with self.assertRaises(SessionError): store.backup('job-1',lambda _:(_ for _ in ()).throw(OSError('interrupted')),lambda _:True)
            with self.assertRaises(SessionError): store.recover_backup('job-1',lambda *_:False,lambda _:True)
            self.assertFalse(store.can_resume('run-1','implement','thread-1',CONFIG,SKILLS,lambda _:True))
            store.close(); store=SessionStore(root)
            self.assertTrue(store.recover_backup('job-1',lambda *_:True,lambda _:True))
            self.assertTrue(store.can_resume('run-1','implement','thread-1',CONFIG,SKILLS,lambda _:True))
            with self.assertRaises(SessionError): store.recover_backup('job-1',lambda *_:True,lambda _:True)
            store.close()
    def test_stale_backup_recovery_cannot_release_a_new_backup(self):
        with tempfile.TemporaryDirectory() as root:
            first=SessionStore(root); first.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            first.bind('job-1','thread-1'); first.release('job-1',lambda _:True)
            def fail(_): raise OSError('backup interrupted')
            with self.assertRaises(SessionError): first.backup('job-1',fail,lambda _:True)
            second=SessionStore(root)
            try:
                def race(*_):
                    second.recover_backup('job-1',lambda *_:True,lambda _:True)
                    with self.assertRaises(SessionError): second.backup('job-1',fail,lambda _:True)
                    return True
                with self.assertRaises(SessionError): first.recover_backup('job-1',race,lambda _:True)
                self.assertFalse(second.can_resume('run-1','implement','thread-1',CONFIG,SKILLS,lambda _:True))
            finally: second.close(); first.close()
    def test_stale_backup_completion_cannot_release_a_new_backup(self):
        with tempfile.TemporaryDirectory() as root:
            first=SessionStore(root); first.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            first.bind('job-1','thread-1'); first.release('job-1',lambda _:True)
            second=SessionStore(root)
            try:
                def race(_):
                    second.recover_backup('job-1',lambda *_:True,lambda _:True)
                    with self.assertRaises(SessionError): second.backup('job-1',lambda _:(_ for _ in ()).throw(OSError('interrupted')),lambda _:True)
                    return 'old-result'
                with self.assertRaises(SessionError): first.backup('job-1',race,lambda _:True)
                self.assertFalse(second.can_resume('run-1','implement','thread-1',CONFIG,SKILLS,lambda _:True))
            finally: second.close(); first.close()
    def test_deletion_fences_uncertain_outcome_and_prevents_resume(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root); store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            store.bind('job-1','thread-1'); store.release('job-1',lambda _:True)
            def uncertain(path): raise OSError('uncertain')
            with self.assertRaises(SessionError): store.delete('job-1',uncertain)
            with self.assertRaises(SessionError): store.acquire('run-1','implement','job-2',CONFIG,SKILLS,session='thread-1')
            with self.assertRaises(SessionError): store.delete('job-1',uncertain)
            store.close()
    def test_database_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'session-state.db').symlink_to('/dev/null')
            with self.assertRaises(SessionError): SessionStore(root)
    def test_unrelated_tasks_and_roles_get_distinct_homes(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root)
            a=store.acquire('task-a','implement','job-a',CONFIG,SKILLS)
            b=store.acquire('task-b','implement','job-b',CONFIG,SKILLS)
            c=store.acquire('task-a','canonical_draft','job-c',CONFIG,SKILLS)
            self.assertEqual(len({a['home'],b['home'],c['home']}),3)
            for job in ('job-a','job-b','job-c'): store.release(job,lambda _:True)
            store.close()
    def test_symlink_or_nested_credential_blocks_reuse(self):
        for name in ('auth.json','nested/credentials.json','.env','escape'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                store=SessionStore(root); entry=store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
                path=Path(entry['home'],name); path.parent.mkdir(parents=True,exist_ok=True)
                if name=='escape': path.symlink_to('/tmp')
                else: path.write_text('synthetic')
                with self.assertRaises(SessionError): store.release('job-1',lambda _:True)
                store.close()
    def test_simultaneous_replacement_claims_have_one_winner(self):
        import concurrent.futures
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root); store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            store.bind('job-1','thread-1'); store.release('job-1',lambda _:True); store.close()
            def claim(job):
                local=SessionStore(root)
                try:
                    try: return local.acquire('run-1','implement',job,CONFIG,SKILLS,session='thread-1',verify_safe=lambda _:True)
                    except SessionError: return None
                finally: local.close()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(claim,['job-2','job-3']))
            self.assertEqual(sum(x is not None for x in results),1)
    def test_separate_fake_server_processes_resume_same_synthetic_home(self):
        import os, subprocess, sys
        from runner.protocol import AppServer
        from runner.stdio import NDJSONTransport
        script=str(Path(__file__).parent/'fixtures'/'fake_persistent_server.py')
        def turn(home,thread=None):
            env=dict(os.environ,SYNTHETIC_STATE_HOME=home)
            process=subprocess.Popen([sys.executable,script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
            try: return AppServer(NDJSONTransport(process.stdout,process.stdin)).run('synthetic','fake',thread_id=thread)
            finally:
                process.stdin.close(); process.wait(timeout=3); process.stdout.close(); process.stderr.close()
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root); first=store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            thread=turn(first['home']); store.bind('job-1',thread); store.release('job-1',lambda _:True)
            second=store.acquire('run-1','implement','job-2',CONFIG,SKILLS,session=thread,verify_safe=lambda _:True)
            self.assertEqual(turn(second['home'],thread),thread)
            store.release('job-2',lambda _:True); store.close()
    def test_confirmed_deletion_removes_only_the_recorded_synthetic_home(self):
        import shutil
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root); entry=store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            outside=Path(root)/'outside'; outside.write_text('untouched')
            with self.assertRaises(SessionError): store.delete('job-1',lambda _:True)
            store.bind('job-1','thread-1'); store.release('job-1',lambda _:True)
            def delete(path): shutil.rmtree(path); return True
            self.assertTrue(store.delete('job-1',delete))
            self.assertFalse(Path(entry['home']).exists()); self.assertEqual(outside.read_text(),'untouched')
            with self.assertRaises(SessionError): store.acquire('run-1','implement','job-2',CONFIG,SKILLS,session='thread-1',verify_safe=lambda _:True)
            store.close()
    def test_resume_capability_requires_exact_binding_and_verified_home(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(root); store.acquire('run-1','implement','job-1',CONFIG,SKILLS)
            store.bind('job-1','thread-1'); store.release('job-1',lambda _:True)
            self.assertTrue(store.can_resume('run-1','implement','thread-1',CONFIG,SKILLS,lambda _:True))
            self.assertFalse(store.can_resume('run-2','implement','thread-1',CONFIG,SKILLS,lambda _:True))
            self.assertFalse(store.can_resume('run-1','implement','thread-1',CONFIG,'c'*64,lambda _:True))
            self.assertFalse(store.can_resume('run-1','implement','thread-1',CONFIG,SKILLS,lambda _:False))
            self.assertFalse(store.can_resume('run-1','qa','thread-1',CONFIG,SKILLS,lambda _:True))
            store.close()
