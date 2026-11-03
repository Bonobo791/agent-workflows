"""Exercise the shipped worker process and authenticated Unix socket locally."""
from contextlib import closing
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

def connect_control(path, timeout):
    deadline=time.monotonic()+10
    while True:
        client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            client.settimeout(timeout)
            client.connect(str(path))
            return client
        except Exception as error:
            client.close()
            if not isinstance(error,(ConnectionRefusedError,FileNotFoundError)) or time.monotonic()>=deadline: raise
            time.sleep(.02)

class WorkerProcessTests(unittest.TestCase):
    def test_connect_control_waits_until_bound_socket_is_listening(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'control.sock'
            server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            server.bind(str(path))
            try:
                with patch('tests.test_worker_process.time.sleep',side_effect=lambda delay:server.listen(1)) as retry:
                    with connect_control(path,1) as client:
                        accepted,_=server.accept();accepted.close()
                    retry.assert_called_once_with(.02)
            finally:
                server.close()

    def test_connect_control_closes_socket_on_nonretryable_failure(self):
        from unittest.mock import Mock,patch
        for method,error in (('connect',PermissionError('denied')),('settimeout',ValueError('invalid timeout'))):
            with self.subTest(method=method):
                client=Mock();getattr(client,method).side_effect=error
                with patch('tests.test_worker_process.socket.socket',return_value=client):
                    with self.assertRaises(type(error)):connect_control('/private/control.sock',1)
                client.close.assert_called_once()

    def test_worker_rejects_symlinked_state_lock(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); data=root/'data'; data.mkdir(mode=0o700)
            secret=root/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            lock=data/'.worker.lock'; lock.symlink_to('missing')
            env={**os.environ,'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_DATA_DIR':str(data),'WORKFLOW_EXECUTION_MODE':'disabled'}
            result=subprocess.run([sys.executable,'-m','runner.worker'],env=env,capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            self.assertTrue(lock.is_symlink())

    def test_separate_control_directory_refuses_existing_legacy_socket_path(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); data=root/'data'; data.mkdir(mode=0o700)
            secret=root/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            legacy=data/'control.sock'; legacy.symlink_to('missing')
            env={**os.environ,'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_DATA_DIR':str(data),'WORKFLOW_CONTROL_DIR':str(root/'other'),'WORKFLOW_EXECUTION_MODE':'disabled'}
            result=subprocess.run([sys.executable,'-m','runner.worker'],env=env,capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            self.assertTrue(legacy.is_symlink())
            self.assertFalse((root/'other'/'control.sock').exists())

    def test_distinct_socket_directories_cannot_share_state(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); data=root/'data'; data.mkdir(mode=0o700)
            secret=root/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            env={**os.environ,'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_DATA_DIR':str(data),'WORKFLOW_CONTROL_DIR':str(root/'first'),'WORKFLOW_EXECUTION_MODE':'disabled'}
            first=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                path=root/'first'/'control.sock'; deadline=time.monotonic()+10
                while not path.exists() and first.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                self.assertTrue(path.is_socket())
                second=subprocess.run([sys.executable,'-m','runner.worker'],env={**env,'WORKFLOW_CONTROL_DIR':str(root/'second')},capture_output=True,text=True,timeout=5)
                self.assertNotEqual(second.returncode,0)
                self.assertFalse((root/'second'/'control.sock').exists())
            finally:
                first.terminate(); first.communicate(timeout=10)

    def test_control_socket_can_use_separate_private_directory(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); data=root/'data'; data.mkdir(mode=0o700)
            sockets=root/'sockets'
            secret=root/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            env={**os.environ,'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_DATA_DIR':str(data),'WORKFLOW_CONTROL_DIR':str(sockets),'WORKFLOW_EXECUTION_MODE':'disabled'}
            process=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                path=sockets/'control.sock'; deadline=time.monotonic()+10
                while not path.exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                self.assertTrue(path.is_socket())
                result=subprocess.run([sys.executable,'-m','runner.cli'],input='{"action":"metrics"}',env=env,capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertTrue(json.loads(result.stdout)['ok'])
            finally:
                process.terminate(); process.communicate(timeout=10)
            self.assertEqual(process.returncode,0)
            self.assertFalse(path.exists())







    def _wait_for_dispatched_stage(self,root,process):
        deadline=time.monotonic()+(100 if os.environ.get('WORKFLOW_TEST_CODEX') else 10)
        while not (root/'stage-entered').exists() and not (root/'stage-error').exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.02)
        failure=(root/'stage-error').read_text() if (root/'stage-error').exists() else 'no stage result'
        self.assertTrue((root/'stage-entered').exists(),failure)


    def _assert_database_fenced(self,root):
        import sqlite3
        with closing(sqlite3.connect(root/'state.db')) as db:
            self.assertEqual(db.execute("SELECT status FROM graph_runs WHERE id='r'").fetchone(),('cancelled',))
            self.assertEqual(db.execute("SELECT status FROM jobs WHERE run='r'").fetchone(),('cancelling',))
            self.assertEqual(db.execute("SELECT state FROM manual_assignments WHERE run='r'").fetchone(),('pending',))
            self.assertIsNone(db.execute("SELECT 1 FROM graph_claims WHERE run='r'").fetchone())
    def _assert_shutdown_fenced(self,root,path):
        self._assert_database_fenced(root)
        self.assertFalse(path.exists())
        from runner.manual_lifecycle import ManualLifecycle, ManualLifecycleError
        lifecycle=ManualLifecycle(str(root/'state.db'),'fixture','one')
        try:
            with self.assertRaises(ManualLifecycleError): lifecycle.claim()
            with self.assertRaises(ManualLifecycleError): lifecycle.confirm_operator_stop('r','one','terminated_and_fenced')
            self.assertEqual(lifecycle.status('r')['status'],'pending')
            if os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1':
                from runner.local_scope import LocalScopes, SystemdScopes, ScopeError
                backend=SystemdScopes(os.environ); scopes=LocalScopes(str(root/'state.db'),backend)
                try:
                    unit=scopes.db.execute("SELECT unit FROM local_scopes WHERE run='r'").fetchone()[0]
                    try: active=scopes.assert_active('r',unit)
                    except ScopeError:
                        if not os.environ.get('WORKFLOW_TEST_CODEX'): raise
                        observed=backend.inspect(unit)
                        self.assertEqual(observed['id'],unit)
                        self.assertEqual(observed['active'],'inactive')
                        self.assertIn(observed['load'],('loaded','not-found'))
                        active=False
                    if active:
                        from types import SimpleNamespace
                        from runner.control import ControlPlane
                        from runner.service import Service
                        service=Service(root,lifecycle=lifecycle)
                        service.model_supervisor=SimpleNamespace(backend=backend)
                        try:
                            control=ControlPlane(service,'x'*40)
                            result=control.handle('x'*40,{'action':'stop_local_scope','run':'r','unit':unit})
                            self.assertEqual(result,{'unit':unit,'state':'terminated'})
                        finally: service.close()
                        self.assertEqual(scopes.state('r',unit),'terminated')
                    if os.environ.get('WORKFLOW_TEST_CODEX'): print('Codex scope active after worker shutdown:',active)
                finally: scopes.close()
                if active: self.assertEqual(lifecycle.confirm_operator_stop('r','one','terminated_and_fenced')['status'],'stopped')
        finally: lifecycle.close()

    def _cleanup_worker_scope(self,root,process,launcher_fd):
        import sqlite3
        if process.poll() is None: process.kill(); process.communicate(timeout=5)
        if launcher_fd is not None:
            import signal
            try: signal.pidfd_send_signal(launcher_fd,signal.SIGKILL)
            except ProcessLookupError: pass
            os.close(launcher_fd)
        if os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1' and (root/'state.db').exists():
            import re
            with closing(sqlite3.connect(root/'state.db')) as db:
                try: rows=db.execute("SELECT unit FROM local_scopes WHERE run='r'").fetchall()
                except sqlite3.OperationalError: rows=[]
            for (unit,) in rows:
                if re.fullmatch(r'agent-workflows-[0-9a-f]{48}\.scope',unit):
                    subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)

    @unittest.skipUnless(os.environ.get('CODEX_TEST_EXECUTABLE'),'real local Codex executable not configured')
    def test_inspected_local_config_starts_and_restarts_worker(self):
        from runner.local_config_inspect import inspect_config
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            secret=Path(root)/'secret';secret.write_text('x'*40);secret.chmod(0o600)
            config=Path(root)/'local.json'
            config.write_text(json.dumps(inspect_config(os.environ['CODEX_TEST_EXECUTABLE'],root,os.environ)));config.chmod(0o600)
            env={key:os.environ[key] for key in ('HOME','PATH','CODEX_HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if key in os.environ}
            env.update(CONTROL_SECRET_FILE=str(secret),WORKFLOW_DATA_DIR=root,WORKFLOW_EXECUTION_MODE='local',WORKFLOW_LIFECYCLE_MODE='manual',MANUAL_WORKER_TARGET='test',MANUAL_WORKER_INCARNATION='one',LOCAL_RUNTIME_CONFIG_FILE=str(config))
            def run_worker(create):
                process=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    path=Path(root)/'control.sock';deadline=time.monotonic()+10
                    while not path.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                    self.assertTrue(path.is_socket())
                    with connect_control(path,3) as client:
                        request={'action':'create','run':'r','workflow':'marketing','task':'t'} if create else {'action':'status','run':'r','stage':'intake'}
                        client.sendall(json.dumps({'secret':'x'*40,'request':request}).encode()+b'\n')
                        with client.makefile('rb') as stream:self.assertTrue(json.loads(stream.readline())['ok'])
                finally:
                    process.terminate()
                    try:stdout,stderr=process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();stdout,stderr=process.communicate(timeout=5)
                self.assertEqual(process.returncode,0,stderr.decode());self.assertNotIn(b'x'*40,stdout+stderr)
                self.assertFalse((Path(root)/'control.sock').exists())
            run_worker(True);run_worker(False)
            with closing(sqlite3.connect(Path(root)/'state.db')) as db:
                self.assertEqual(db.execute("SELECT workflow,status FROM graph_runs WHERE id='r'").fetchone(),('marketing','active'))
    def test_local_mode_starts_only_with_private_pinned_configuration(self):
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            secret=Path(root)/'secret';secret.write_text('x'*40);secret.chmod(0o600)
            binary=Path(root)/'codex';binary.write_bytes(b'synthetic');binary.chmod(0o700)
            config=Path(root)/'local.json';config.write_text(json.dumps({'codex_executable':str(binary),'codex_sha256':hashlib.sha256(b'synthetic').hexdigest(),'disabled_integrations':{'mcp_servers':[],'plugins':[]}}));config.chmod(0o600)
            env={**os.environ,'CONTROL_SECRET_FILE':str(secret),'WORKFLOW_DATA_DIR':root,'WORKFLOW_EXECUTION_MODE':'local','WORKFLOW_LIFECYCLE_MODE':'manual','MANUAL_WORKER_TARGET':'test','MANUAL_WORKER_INCARNATION':'one','LOCAL_RUNTIME_CONFIG_FILE':str(config)}
            process=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                path=Path(root)/'control.sock';deadline=time.monotonic()+10
                while not path.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(path.is_socket())
                with connect_control(path,3) as client:
                    client.sendall(json.dumps({'secret':'x'*40,'request':{'action':'create','run':'r','workflow':'marketing','task':'t'}}).encode()+b'\n')
                    with client.makefile('rb') as stream:self.assertTrue(json.loads(stream.readline())['ok'])
            finally:
                process.terminate()
                try: stdout,stderr=process.communicate(timeout=5)
                except subprocess.TimeoutExpired:process.kill();stdout,stderr=process.communicate(timeout=5)
            self.assertEqual(process.returncode,0,stderr.decode());self.assertIn(b'execution mode: local',stdout)
            data=json.loads(config.read_text());data['codex_sha256']='0'*64;config.write_text(json.dumps(data))
            failed=subprocess.run([sys.executable,'-m','runner.worker'],env=env,capture_output=True,timeout=10)
            self.assertNotEqual(failed.returncode,0);self.assertNotIn(b'x'*40,failed.stderr)
    def test_real_worker_socket_authentication_and_control(self):
        with tempfile.TemporaryDirectory() as root:
            secret=Path(root)/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            env=os.environ.copy(); env.update(CONTROL_SECRET_FILE=str(secret),WORKFLOW_DATA_DIR=root,WORKFLOW_EXECUTION_MODE='disabled')
            process=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                path=str(Path(root)/'control.sock'); deadline=time.monotonic()+10
                while not Path(path).exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                self.assertTrue(Path(path).is_socket(), 'worker socket was not created')
                def request(token,body):
                    with connect_control(path,3) as client:
                        client.sendall(json.dumps({'secret':token,'request':body}).encode()+b'\n')
                        with client.makefile('rb') as stream: return json.loads(stream.readline())
                self.assertFalse(request('wrong',{'action':'create','run':'r','workflow':'marketing','task':'t'})['ok'])
                self.assertFalse(request('x'*40,{'action':'start_configured_auto'})['ok'])
                self.assertTrue(request('x'*40,{'action':'create','run':'r','workflow':'marketing','task':'t'})['ok'])
                self.assertTrue(request('x'*40,{'action':'status','run':'r','stage':'intake'})['ok'])
                self.assertIn('workflow_expired_leases 0',request('x'*40,{'action':'metrics'})['result']['metrics'])
                metrics=subprocess.run([sys.executable,'-m','runner.metrics_cli'],env=env,capture_output=True,text=True,timeout=5)
                self.assertEqual(metrics.returncode,0); self.assertIn('workflow_expired_leases 0',metrics.stdout)
                self.assertNotIn('x'*40,metrics.stdout+metrics.stderr)
                wrong=Path(root)/'wrong'; wrong.write_text('y'*40); wrong.chmod(0o600)
                failed=subprocess.run([sys.executable,'-m','runner.metrics_cli'],env={**env,'CONTROL_SECRET_FILE':str(wrong)},capture_output=True,text=True,timeout=5)
                self.assertNotEqual(failed.returncode,0); self.assertEqual(failed.stdout,''); self.assertNotIn('y'*40,failed.stderr)
            finally:
                process.terminate()
                try: process.communicate(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.communicate(timeout=5)
            self.assertEqual(process.returncode,0)
