import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import shutil
from types import SimpleNamespace
import unittest
import uuid

from runner.local_launch import GuardedLaunch, LaunchError, start_guarded


def add_recovery_fence(scopes):
    scopes.db.executescript("""CREATE TABLE manual_assignments(run TEXT,target TEXT,incarnation TEXT,state TEXT);
        CREATE TABLE manual_workers(target TEXT,incarnation TEXT,state TEXT);
        CREATE TABLE jobs(run TEXT,status TEXT);
        CREATE TABLE graph_runs(id TEXT,status TEXT);
        CREATE TABLE graph_claims(run TEXT);
        INSERT INTO manual_assignments VALUES('run','target','inc','pending');
        INSERT INTO manual_workers VALUES('target','inc','assigned');
        INSERT INTO jobs VALUES('run','uncertain');
        INSERT INTO graph_runs VALUES('run','cancelled');""")


class LocalLaunchTests(unittest.TestCase):
    def test_receipt_ready_at_deadline_is_rejected(self):
        import json
        from unittest.mock import patch
        ack_read,ack_write=os.pipe();go_read,go_write=os.pipe()
        launch=GuardedLaunch(SimpleNamespace(pid=123,poll=lambda:None),ack_read,go_write,'a'*64)
        os.write(ack_write,json.dumps({'version':1,'nonce':'a'*64,'pid':123}).encode());os.close(ack_write)
        try:
            with patch('runner.local_launch.time.monotonic',side_effect=[0,0,0,0,2]):
                with self.assertRaises(LaunchError):launch.receive(1,lambda:False)
            self.assertIsNone(launch.receipt)
        finally:launch.close();os.close(go_read)

    def test_target_receives_only_explicit_environment(self):
        env={**os.environ,'WORKFLOW_TEST_SECRET':'private'}
        code="import os; print(os.environ.get('WORKFLOW_TEST_SECRET','absent'),os.environ['WORKFLOW_TEST_ALLOWED'])"
        launch=start_guarded(['/usr/bin/env','-i','WORKFLOW_TEST_ALLOWED=public'],
                             [sys.executable,'-c',code],cwd=None,env=env)
        try:
            launch.receive(time.monotonic()+5,lambda:False);launch.release()
            stdout,_=launch.process.communicate(timeout=5)
            self.assertEqual(launch.process.returncode,0)
            self.assertEqual(stdout,b'absent public\n')
        finally:
            launch.close()
            if launch.process.poll() is None:launch.process.kill();launch.process.wait(timeout=5)
            launch.process.stdin.close();launch.process.stdout.close()

    def test_target_waits_for_receipt_and_release(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'target'
            code='from pathlib import Path; Path('+repr(str(marker))+').touch()'
            launch=start_guarded([], [sys.executable,'-c',code], cwd=root, env=dict(os.environ))
            try:
                receipt=launch.receive(time.monotonic()+5,lambda:False)
                self.assertEqual(receipt['pid'],launch.process.pid)
                self.assertFalse(marker.exists())
                launch.release()
                self.assertEqual(launch.process.wait(timeout=5),0)
                self.assertTrue(marker.exists())
            finally:
                launch.close()
                if launch.process.poll() is None:launch.process.kill();launch.process.wait(timeout=5)
                launch.process.stdin.close();launch.process.stdout.close()

    def test_environment_prefix_excludes_manager_secrets_from_target(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'environment'
            code='import os; from pathlib import Path; Path('+repr(str(marker))+').write_text(os.environ.get("ACCESS_TOKEN","missing"))'
            manager=dict(os.environ);manager['ACCESS_TOKEN']='synthetic-secret'
            launch=start_guarded(['/usr/bin/env','-i','HOME='+root,'PATH=/usr/bin:/bin'],[sys.executable,'-c',code],cwd=root,env=manager)
            try:
                launch.receive(time.monotonic()+5,lambda:False);launch.release()
                self.assertEqual(launch.process.wait(timeout=5),0);self.assertEqual(marker.read_text(),'missing')
            finally:
                launch.close()
                if launch.process.poll() is None:launch.process.kill();launch.process.wait(timeout=5)
                launch.process.stdin.close();launch.process.stdout.close()

    def test_cancellation_closes_go_pipe_and_prevents_target(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'target'
            code='from pathlib import Path; Path('+repr(str(marker))+').touch()'
            launch=start_guarded([], [sys.executable,'-c',code], cwd=root, env=dict(os.environ))
            try:
                deadline=time.monotonic()+5
                with self.assertRaises(LaunchError):launch.receive(deadline,lambda:True)
                launch.close()
                self.assertNotEqual(launch.process.wait(timeout=5),0)
                self.assertFalse(marker.exists())
            finally:
                launch.close()
                if launch.process.poll() is None:launch.process.kill();launch.process.wait(timeout=5)
                launch.process.stdin.close();launch.process.stdout.close()

    def test_release_before_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            launch=start_guarded([], [sys.executable,'-c','pass'], cwd=root, env=dict(os.environ))
            try:
                with self.assertRaises(LaunchError):launch.release()
            finally:
                launch.close()
                if launch.process.poll() is None:launch.process.wait(timeout=5)
                launch.process.stdin.close();launch.process.stdout.close()

    def test_guard_does_not_start_target_after_configured_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'target';code='from pathlib import Path; Path('+repr(str(marker))+').touch()'
            deadline=time.monotonic()+1
            launch=start_guarded([], [sys.executable,'-c',code],cwd=root,env=dict(os.environ),deadline=deadline)
            try:
                launch.receive(deadline,lambda:False)
                self.assertNotEqual(launch.process.wait(timeout=5),0)
                with self.assertRaises(LaunchError):launch.release()
                self.assertFalse(marker.exists())
            finally:
                launch.close()
                if launch.process.poll() is None:launch.process.kill();launch.process.wait(timeout=5)
                launch.process.stdin.close();launch.process.stdout.close()

    def test_mutated_launch_identity_cannot_release_target(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'target'
            code='from pathlib import Path; Path('+repr(str(marker))+').touch()'
            launch=start_guarded([], [sys.executable,'-c',code], cwd=root, env=dict(os.environ))
            try:
                launch.receive(time.monotonic()+5,lambda:False)
                launch.nonce='b'*64
                with self.assertRaises(LaunchError):launch.release()
                launch.close()
                self.assertNotEqual(launch.process.wait(timeout=5),0)
                self.assertFalse(marker.exists())
            finally:
                launch.close()
                if launch.process.poll() is None:launch.process.kill();launch.process.wait(timeout=5)
                launch.process.stdin.close();launch.process.stdout.close()

    def test_malformed_or_mismatched_receipts_never_release_target(self):
        nonce='a'*64
        cases=(b'',b'not-json\n',b'x'*513,
               ('{"version":1,"nonce":"'+nonce+'","pid":124}\n').encode(),
               ('{"version":true,"nonce":"'+nonce+'","pid":123}\n').encode(),
               ('{"version":1,"nonce":"'+nonce+'","pid":123,"extra":1}\n').encode())
        for payload in cases:
            with self.subTest(payload=payload[:50]):
                ack_read,ack_write=os.pipe();go_read,go_write=os.pipe()
                launch=GuardedLaunch(SimpleNamespace(pid=123,poll=lambda:None),ack_read,go_write,nonce)
                try:
                    if payload:os.write(ack_write,payload)
                    os.close(ack_write);ack_write=None
                    deadline=time.monotonic()+2
                    with self.assertRaises(LaunchError):launch.receive(deadline,lambda:False)
                    with self.assertRaises(LaunchError):launch.release()
                    launch.close()
                    self.assertEqual(os.read(go_read,1),b'')
                finally:
                    if ack_write is not None:os.close(ack_write)
                    os.close(go_read);launch.close()

    def test_receipt_arriving_at_deadline_cannot_authorize_target(self):
        from unittest.mock import patch
        nonce='a'*64;ack_read,ack_write=os.pipe();go_read,go_write=os.pipe()
        launch=GuardedLaunch(SimpleNamespace(pid=123,poll=lambda:None),ack_read,go_write,nonce)
        try:
            os.write(ack_write,('{"version":1,"nonce":"'+nonce+'","pid":123}').encode());os.close(ack_write)
            with patch('runner.local_launch.time.monotonic',side_effect=[0,0,0,0,1]):
                with self.assertRaises(LaunchError):launch.receive(1,lambda:False)
            with self.assertRaises(LaunchError):launch.release()
            launch.close();self.assertEqual(os.read(go_read,1),b'')
        finally:
            os.close(go_read);launch.close()

    def test_failure_cleanup_reaps_child_that_exits_after_short_wait(self):
        from runner.local_launch import reap_after_failure
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(1.5)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            reap_after_failure(process)
            deadline=time.monotonic()+5
            while process.returncode is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertEqual(process.returncode,0)
            with self.assertRaises(ChildProcessError):os.waitpid(process.pid,os.WNOHANG)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)

    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_guarded_launch_receipt_matches_real_scope_before_release(self):
        from runner.local_scope import LocalScopes, SystemdScopes
        backend=SystemdScopes(os.environ)
        with tempfile.TemporaryDirectory(prefix='workflow-guarded-launch-') as root:
            scopes=LocalScopes(str(Path(root)/'state.db'),backend)
            unit=scopes.reserve('run')
            marker=Path(root)/'target';code='from pathlib import Path; Path('+repr(str(marker))+').touch()'
            prefix=['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--','/usr/bin/env','-i',
                    'HOME='+os.environ['HOME'],'PATH=/usr/bin:/bin']
            launch=start_guarded(prefix,[sys.executable,'-c',code],cwd=root,env=dict(os.environ))
            try:
                receipt=launch.receive(time.monotonic()+10,lambda:False)
                observed=backend.inspect(unit)
                self.assertEqual((observed['id'],observed['load'],observed['active']),(unit,'loaded','active'))
                self.assertIn('0::'+observed['cgroup'],(Path('/proc')/str(receipt['pid'])/'cgroup').read_text().splitlines())
                self.assertTrue(backend.pid_in_cgroup(receipt['pid'],observed['cgroup']))
                self.assertFalse(backend.pid_in_cgroup(receipt['pid'],observed['cgroup']+'-other'))
                self.assertFalse(marker.exists())
                scopes.bind_confirmed('run',unit,launch)
                self.assertEqual(scopes.state('run',unit),'running')
                launch.release();self.assertEqual(launch.process.wait(timeout=10),0)
                self.assertTrue(marker.exists())
            finally:
                launch.close()
                if launch.process.poll() is None:
                    subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
                    try:launch.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:launch.process.kill();launch.process.wait(timeout=5)
                launch.process.stdin.close();launch.process.stdout.close()
                scopes.close()

    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_caller_killed_after_binding_never_starts_target(self):
        from runner.local_scope import LocalScopes, SystemdScopes
        code='''import os,sys,time
from pathlib import Path
from runner.local_launch import start_guarded
from runner.local_scope import LocalScopes,SystemdScopes
root=Path(sys.argv[1]);scopes=LocalScopes(str(root/'state.db'),SystemdScopes(os.environ))
unit=scopes.reserve('run');(root/'unit').write_text(unit)
prefix=['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--']
target=[sys.executable,'-c',"from pathlib import Path; Path(%r).touch()" % str(root/'target')]
launch=start_guarded(prefix,target,cwd=str(root),env=dict(os.environ))
launch.receive(time.monotonic()+10,lambda:False)
scopes.bind_confirmed('run',unit,launch)
(root/'bound').touch()
time.sleep(60)
'''
        root=Path(tempfile.mkdtemp(prefix='workflow-bound-crash-'));backend=SystemdScopes(os.environ)
        caller=None;group=None;unit=None;safe=False
        try:
            with tempfile.TemporaryFile() as errors:
                caller=subprocess.Popen([sys.executable,'-c',code,str(root)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=errors)
                deadline=time.monotonic()+15
                while not (root/'bound').exists() and caller.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                if not (root/'bound').exists():
                    errors.seek(0);self.fail('binding unavailable: '+errors.read(2000).decode(errors='replace'))
            unit=(root/'unit').read_text();scopes=LocalScopes(str(root/'state.db'),backend)
            try:
                row=scopes.db.execute('SELECT cgroup,state FROM local_scopes WHERE unit=?',(unit,)).fetchone()
                self.assertEqual(row[1],'running');group=row[0]
                self.assertEqual(scopes.db.execute('SELECT COUNT(*) FROM local_scope_launch_receipts WHERE unit=?',(unit,)).fetchone(),(1,))
            finally:scopes.close()
            caller.kill();self.assertEqual(caller.wait(timeout=5),-9)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                observed=backend.inspect(unit)
                if observed['active']=='inactive' and backend.empty_cgroup(group):break
                time.sleep(.05)
            self.assertEqual(observed['active'],'inactive');self.assertTrue(backend.empty_cgroup(group))
            self.assertFalse((root/'target').exists())
            scopes=LocalScopes(str(root/'state.db'),backend)
            try:
                add_recovery_fence(scopes)
                self.assertTrue(scopes.reconcile_confirmed('run',unit,('target','inc')))
                self.assertEqual(scopes.state('run',unit),'terminated')
            finally:scopes.close()
            safe=True
        finally:
            if caller is not None and caller.poll() is None:caller.kill();caller.wait(timeout=5)
            if unit is None and (root/'unit').exists():unit=(root/'unit').read_text()
            if unit and not safe:
                try:
                    observed=backend.inspect(unit);group=group or observed.get('cgroup')
                    if observed['active']=='active':backend.stop(unit)
                    observed=backend.inspect(unit)
                    safe=bool(group and observed['active']=='inactive' and backend.empty_cgroup(group))
                except Exception:pass
            if safe:shutil.rmtree(root)

    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_released_target_remains_bound_when_caller_is_killed(self):
        from runner.local_scope import LocalScopes, SystemdScopes
        code='''import os,sys,time
from pathlib import Path
from runner.local_launch import start_guarded
from runner.local_scope import LocalScopes,SystemdScopes
root=Path(sys.argv[1]);scopes=LocalScopes(str(root/'state.db'),SystemdScopes(os.environ))
unit=scopes.reserve('run');(root/'unit').write_text(unit)
prefix=['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--']
target=[sys.executable,'-c',"from pathlib import Path; import time; Path(%r).touch(); p=Path(%r); exec('while not p.exists(): time.sleep(.05)')" % (str(root/'target'),str(root/'finish'))]
launch=start_guarded(prefix,target,cwd=str(root),env=dict(os.environ))
launch.receive(time.monotonic()+10,lambda:False);scopes.bind_confirmed('run',unit,launch);launch.release()
time.sleep(60)
'''
        root=Path(tempfile.mkdtemp(prefix='workflow-released-crash-'));backend=SystemdScopes(os.environ)
        caller=None;unit=None;group=None;safe=False
        try:
            with tempfile.TemporaryFile() as errors:
                caller=subprocess.Popen([sys.executable,'-c',code,str(root)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=errors)
                deadline=time.monotonic()+15
                while not (root/'target').exists() and caller.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                if not (root/'target').exists():
                    errors.seek(0);self.fail('target unavailable: '+errors.read(2000).decode(errors='replace'))
            unit=(root/'unit').read_text();scopes=LocalScopes(str(root/'state.db'),backend)
            try:
                row=scopes.db.execute('SELECT cgroup,state FROM local_scopes WHERE unit=?',(unit,)).fetchone()
                self.assertEqual(row[1],'running');group=row[0]
                caller.kill();self.assertEqual(caller.wait(timeout=5),-9)
                self.assertEqual(backend.inspect(unit)['active'],'active')
                (root/'finish').touch();deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    observed=backend.inspect(unit)
                    if observed['active']=='inactive' and backend.empty_cgroup(group):break
                    time.sleep(.05)
                self.assertEqual(observed['active'],'inactive');self.assertTrue(backend.empty_cgroup(group))
                add_recovery_fence(scopes)
                self.assertTrue(scopes.reconcile_confirmed('run',unit,('target','inc')));safe=True
            finally:scopes.close()
        finally:
            if caller is not None and caller.poll() is None:caller.kill();caller.wait(timeout=5)
            if unit is None and (root/'unit').exists():unit=(root/'unit').read_text()
            if unit and not safe:
                try:
                    observed=backend.inspect(unit);group=group or observed.get('cgroup')
                    if observed['active']=='active':backend.stop(unit)
                    observed=backend.inspect(unit)
                    safe=bool(group and observed['active']=='inactive' and backend.empty_cgroup(group))
                except Exception:pass
            if safe:shutil.rmtree(root)
