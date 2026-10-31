import unittest
from runner.docker_controller import DockerController, DockerError

ID = 'a' * 64

class Engine:
    def __init__(self):
        self.calls=[]; self.running=True; self.labels={'agent-workflows.run':'run-1'}
    def request(self,method,path,body=None):
        self.calls.append((method,path,body))
        if path.endswith('/json'):
            return 200, {'Id': ID, 'Config': {'Labels': self.labels}, 'State': {'Running': self.running, 'Status': 'running' if self.running else 'exited'}}
        if '/stop?' in path or '/kill?' in path: self.running=False; return 204, None
        if '/wait?' in path: return 200, {'StatusCode': 137}
        raise AssertionError(path)

class DockerControllerTests(unittest.TestCase):
    def test_stop_inspects_exact_id_and_label_then_waits_and_rechecks(self):
        engine=Engine(); controller=DockerController(engine, {'run-1': ID})
        self.assertTrue(controller.stop('run-1'))
        self.assertEqual([method for method,_,_ in engine.calls], ['GET','POST','POST','GET'])
        self.assertTrue(all(ID in path for _,path,_ in engine.calls))

    def test_identity_mismatch_never_sends_stop(self):
        engine=Engine(); engine.labels={'agent-workflows.run':'other'}
        with self.assertRaises(DockerError): DockerController(engine, {'run-1': ID}).stop('run-1')
        self.assertEqual(len(engine.calls), 1)

    def test_stop_failure_is_uncertain_and_never_retried(self):
        class Failing(Engine):
            def request(self,method,path,body=None):
                if '/stop?' in path: self.calls.append((method,path,body)); raise OSError('lost response')
                return super().request(method,path,body)
        engine=Failing()
        with self.assertRaises(DockerError): DockerController(engine, {'run-1': ID}).stop('run-1')
        self.assertEqual(len(engine.calls),2)

    def test_missing_record_or_short_id_rejected(self):
        with self.assertRaises(DockerError): DockerController(Engine(), {'run-1':'abc'})
        with self.assertRaises(DockerError): DockerController(Engine(), {}).stop('run-1')

class UnixTransportTests(unittest.TestCase):
    def test_render_wait_uses_its_own_bounded_timeout(self):
        import http.server, json, os, socketserver, tempfile, threading, time
        from runner.docker_controller import UnixEngine
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                time.sleep(.2); data=json.dumps({'StatusCode':0}).encode()
                try:
                    self.send_response(200); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
                except BrokenPipeError: pass
            def log_message(self,*args): pass
        class Server(socketserver.ThreadingUnixStreamServer): daemon_threads=True
        with tempfile.TemporaryDirectory() as root:
            server=Server(os.path.join(root,'engine.sock'),Handler); thread=threading.Thread(target=server.serve_forever); thread.start()
            try:
                engine=UnixEngine(os.path.join(root,'engine.sock'),timeout=.05)
                self.assertEqual(engine.request('POST','/containers/'+ID+'/wait?condition=not-running',timeout=1),(200,{'StatusCode':0}))
                started=time.monotonic()
                with self.assertRaises(DockerError): engine.request('POST','/containers/'+ID+'/wait?condition=not-running',timeout=.05)
                self.assertLess(time.monotonic()-started,.6)
                with self.assertRaises(DockerError): engine.request('GET','/containers/'+ID+'/json',timeout=1)
                for timeout in (0,-1,601,True,float('nan'),float('inf')):
                    with self.subTest(timeout=timeout),self.assertRaises(DockerError):
                        engine.request('POST','/containers/'+ID+'/wait?condition=not-running',timeout=timeout)
            finally: server.shutdown(); thread.join(); server.server_close()
    def test_http_over_unix_socket(self):
        import http.server
        import json
        import os
        import socketserver
        import tempfile
        import threading
        from runner.docker_controller import UnixEngine
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'path':self.path}).encode())
            def log_message(self,*args): pass
        class Server(socketserver.UnixStreamServer): pass
        with tempfile.TemporaryDirectory() as root:
            path=os.path.join(root,'engine.sock'); server=Server(path,Handler)
            thread=threading.Thread(target=server.serve_forever); thread.start()
            try:
                status,data=UnixEngine(path).request('GET','/containers/'+ID+'/json')
                self.assertEqual(status,200)
                self.assertEqual(data['path'],'/v1.49/containers/'+ID+'/json')
            finally: server.shutdown(); thread.join(); server.server_close()

    def test_rejects_invalid_path_before_connecting(self):
        from runner.docker_controller import UnixEngine
        with self.assertRaises(DockerError): UnixEngine('/tmp/nonexistent').request('GET','//outside')

    def test_transport_rejects_wrong_verb_for_operation(self):
        from runner.docker_controller import UnixEngine
        with self.assertRaisesRegex(DockerError, 'unsupported Engine request'): UnixEngine('/tmp/nonexistent').request('POST','/containers/'+ID+'/json')
        with self.assertRaisesRegex(DockerError, 'unsupported Engine request'): UnixEngine('/tmp/nonexistent').request('GET','/containers/'+ID+'/stop?t=10')

    def test_full_stop_over_fake_http_engine(self):
        import http.server
        import json
        import os
        import socketserver
        import tempfile
        import threading
        from runner.docker_controller import UnixEngine
        calls=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            running=True
            def reply(self,status,value=None):
                self.send_response(status); self.end_headers()
                if value is not None: self.wfile.write(json.dumps(value).encode())
            def do_GET(self):
                calls.append(('GET',self.path))
                self.reply(200,{'Id':ID,'Config':{'Labels':{'agent-workflows.run':'run-1'}},'State':{'Running':Handler.running,'Status':'running' if Handler.running else 'exited'}})
            def do_POST(self):
                calls.append(('POST',self.path))
                if '/stop?' in self.path: Handler.running=False; self.reply(204)
                else: self.reply(200,{'StatusCode':0})
            def log_message(self,*args): pass
        class Server(socketserver.UnixStreamServer): pass
        with tempfile.TemporaryDirectory() as root:
            path=os.path.join(root,'engine.sock'); server=Server(path,Handler)
            thread=threading.Thread(target=server.serve_forever); thread.start()
            try: self.assertTrue(DockerController(UnixEngine(path),{'run-1':ID}).stop('run-1'))
            finally: server.shutdown(); thread.join(); server.server_close()
        self.assertEqual([method for method,_ in calls],['GET','POST','POST','GET'])
        self.assertTrue(all(path.startswith('/v1.49/containers/'+ID+'/') for _,path in calls))

class AssignmentTests(unittest.TestCase):
    def test_create_records_id_before_start_and_recovers_after_restart(self):
        import tempfile
        from runner.docker_controller import DockerAssignments
        class CreateEngine(Engine):
            def request(self,method,path,body=None):
                if path=='/containers/create':
                    self.calls.append((method,path,body)); self.running=False
                    return 201,{'Id':ID,'Warnings':[]}
                if path.endswith('/json'):
                    self.calls.append((method,path,body))
                    return 200,{'Id':ID,'Config':{'Labels':self.labels},'State':{'Running':self.running,'Status':'running' if self.running else 'created'}}
                if path.endswith('/start'):
                    self.calls.append((method,path,body)); self.running=True; return 204,None
                return super().request(method,path,body)
        with tempfile.TemporaryDirectory() as root:
            engine=CreateEngine(); assignments=DockerAssignments(root+'/state.db',engine,'example@sha256:'+'1'*64)
            self.assertEqual(assignments.create('run-1'),ID)
            self.assertEqual(assignments.get('run-1'),{'id':ID,'state':'created'})
            assignments.close()
            assignments=DockerAssignments(root+'/state.db',engine,'example@sha256:'+'1'*64)
            self.assertTrue(assignments.start('run-1'))
            self.assertEqual(assignments.get('run-1'),{'id':ID,'state':'running'})
            self.assertEqual(engine.calls[0][2]['Labels']['agent-workflows.run'],'run-1')
            self.assertNotIn('Binds',engine.calls[0][2]['HostConfig'])
            assignments.close()

    def test_uncertain_create_is_durable_and_cannot_be_retried(self):
        import tempfile
        from runner.docker_controller import DockerAssignments
        class Lost(Engine):
            def request(self,method,path,body=None): self.calls.append((method,path,body)); raise OSError('lost')
        with tempfile.TemporaryDirectory() as root:
            engine=Lost(); assignments=DockerAssignments(root+'/state.db',engine,'example@sha256:'+'1'*64)
            with self.assertRaises(DockerError): assignments.create('run-1')
            assignments.close(); assignments=DockerAssignments(root+'/state.db',engine,'example@sha256:'+'1'*64)
            with self.assertRaises(DockerError): assignments.create('run-1')
            self.assertEqual(len(engine.calls),1)
            self.assertEqual(assignments.get('run-1')['state'],'creating')
            assignments.close()

    def test_start_refuses_wrong_label_without_mutation(self):
        import tempfile
        from runner.docker_controller import DockerAssignments
        class Wrong(Engine):
            def request(self,method,path,body=None):
                if path=='/containers/create': self.calls.append((method,path,body)); return 201,{'Id':ID}
                return super().request(method,path,body)
        with tempfile.TemporaryDirectory() as root:
            engine=Wrong(); assignments=DockerAssignments(root+'/state.db',engine,'example@sha256:'+'1'*64)
            assignments.create('run-1'); engine.labels={'agent-workflows.run':'other'}
            with self.assertRaises(DockerError): assignments.start('run-1')
            self.assertFalse(any(path.endswith('/start') for _,path,_ in engine.calls))
            assignments.close()

    def test_lost_start_is_fenced_across_restart(self):
        import tempfile
        from runner.docker_controller import DockerAssignments
        class LostStart(Engine):
            def request(self,method,path,body=None):
                if path=='/containers/create': return 201,{'Id':ID}
                if path.endswith('/json'): return 200,{'Id':ID,'Config':{'Labels':self.labels},'State':{'Running':False,'Status':'created'}}
                self.calls.append((method,path,body)); raise OSError('lost')
        with tempfile.TemporaryDirectory() as root:
            engine=LostStart(); assignment=DockerAssignments(root+'/db',engine,'example@sha256:'+'1'*64)
            assignment.create('run-1')
            with self.assertRaises(DockerError): assignment.start('run-1')
            assignment.close(); assignment=DockerAssignments(root+'/db',engine,'example@sha256:'+'1'*64)
            with self.assertRaises(DockerError): assignment.start('run-1')
            self.assertEqual(assignment.get('run-1')['state'],'starting')
            self.assertEqual(len(engine.calls),1); assignment.close()

    def test_create_start_through_fake_http_engine(self):
        import http.server, json, os, socketserver, tempfile, threading
        from runner.docker_controller import DockerAssignments, UnixEngine
        calls=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            running=False
            def reply(self,status,value=None):
                self.send_response(status); self.end_headers()
                if value is not None: self.wfile.write(json.dumps(value).encode())
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length',0)))
                calls.append((self.path,json.loads(body) if body else None))
                if self.path.endswith('/containers/create'): self.reply(201,{'Id':ID})
                else: Handler.running=True; self.reply(204)
            def do_GET(self):
                self.reply(200,{'Id':ID,'Config':{'Labels':{'agent-workflows.run':'run-1'}},'State':{'Running':Handler.running,'Status':'running' if Handler.running else 'created'}})
            def log_message(self,*args): pass
        class Server(socketserver.UnixStreamServer): pass
        with tempfile.TemporaryDirectory() as root:
            path=os.path.join(root,'engine.sock'); server=Server(path,Handler)
            thread=threading.Thread(target=server.serve_forever); thread.start()
            try:
                assignment=DockerAssignments(root+'/db',UnixEngine(path),'example@sha256:'+'1'*64)
                self.assertEqual(assignment.create('run-1'),ID); self.assertTrue(assignment.start('run-1')); assignment.close()
            finally: server.shutdown(); thread.join(); server.server_close()
        self.assertEqual(calls[0][0],'/v1.49/containers/create')
        self.assertEqual(calls[0][1]['Labels']['agent-workflows.run'],'run-1')
        self.assertEqual(calls[1][0],'/v1.49/containers/'+ID+'/start')

    def test_attach_checks_recorded_id_and_run_label(self):
        import tempfile
        from runner.docker_controller import DockerAssignments
        class Attaching(Engine):
            def request(self,method,path,body=None):
                if path=='/containers/create': return 201,{'Id':ID}
                return super().request(method,path,body)
            def attach(self,cid): self.calls.append(('ATTACH',cid,None)); return 'stream'
        with tempfile.TemporaryDirectory() as root:
            engine=Attaching(); assignment=DockerAssignments(root+'/db',engine,'example@sha256:'+'1'*64)
            assignment.create('run-1'); engine.labels={'agent-workflows.run':'wrong'}
            with self.assertRaises(DockerError): assignment.attach('run-1')
            self.assertFalse(any(method=='ATTACH' for method,_,_ in engine.calls))
            engine.labels={'agent-workflows.run':'run-1'}
            self.assertEqual(assignment.attach('run-1'),'stream'); assignment.close()

    def test_disposable_create_binds_approved_policy_and_exact_generated_mounts(self):
        from runner.actions import ActionLedger
        from runner.docker_config import DockerConfig
        from runner.docker_controller import DockerAssignments
        import tempfile
        from pathlib import Path
        class Create(Engine):
            def request(self,method,path,body=None):
                self.calls.append((method,path,body)); return 201,{'Id':ID}
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)},root)
            engine=Create(); assignments=DockerAssignments(root+'/db',engine,config.image); mounts=config.create_job_dirs('job-1')
            with self.assertRaises(DockerError): assignments.create_disposable('job-1',config,mounts)
            ledger=ActionLedger(root+'/db'); ledger.approve('controller','docker_config',config.policy_hash,'operator')
            self.assertEqual(assignments.create_disposable('job-1',config,mounts),ID)
            self.assertEqual(assignments.db.execute('SELECT container_id,policy_hash FROM docker_disposable_assignments WHERE run=?',('job-1',)).fetchone(),(ID,config.policy_hash))
            spec=engine.calls[0][2]; self.assertIn('model_providers.openai_chatgpt_plan.requires_openai_auth=false',spec['Cmd'])
            self.assertIn('shell_environment_policy.inherit="none"',spec['Cmd'])
            self.assertNotIn('Env',spec)
            self.assertEqual(spec['HostConfig']['RestartPolicy']['Name'],'no')
            self.assertEqual({m['Source'] for m in spec['HostConfig']['Mounts']},set(mounts.values()))
            ledger.db.close(); assignments.close()

    def test_openfilm_job_requires_exact_approval_and_has_no_network_or_credentials(self):
        import tempfile
        from pathlib import Path
        from runner.actions import ActionLedger
        from runner.docker_config import DockerConfig
        from runner.docker_controller import DockerAssignments
        class Create(Engine):
            def request(self,method,path,body=None): self.calls.append((method,path,body)); return 201,{'Id':ID}
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':'example@sha256:'+'1'*64,'network':'aw-test','job_root':str(jobs)},root)
            engine=Create(); assignments=DockerAssignments(root+'/db',engine,config.image)
            assignments.db.execute('CREATE TABLE docker_execution_map(task_run TEXT,role TEXT,assignment TEXT PRIMARY KEY)')
            assignments.db.execute('CREATE TABLE openfilm_render_jobs(assignment TEXT PRIMARY KEY,task_run TEXT,attempt_id TEXT,policy_hash TEXT,state TEXT)')
            assignments.db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',('task','openfilm_render','job'))
            assignments.db.execute('INSERT INTO openfilm_render_jobs VALUES(?,?,?,?,?)',('job','task','attempt','policy','launching'))
            mounts=config.create_job_dirs('job'); ledger=ActionLedger(root+'/db')
            ledger.approve('controller','docker_config',config.policy_hash,'operator')
            params={'task':'task','attempt_id':'attempt','policy_hash':'policy'}
            with self.assertRaises(DockerError): assignments.create_disposable('job',config,mounts,openfilm=params)
            self.assertFalse(engine.calls)
            ledger.approve('controller','openfilm_policy','policy','operator')
            with self.assertRaises(DockerError): assignments.create_disposable('job',config,mounts,openfilm={**params,'attempt_id':'other'})
            self.assertFalse(engine.calls)
            assignments.create_disposable('job',config,mounts,openfilm=params)
            spec=engine.calls[0][2]
            self.assertEqual(spec['Entrypoint'],['node','/app/runner/openfilm_render.mjs'])
            self.assertEqual(spec['HostConfig']['NetworkMode'],'none')
            self.assertTrue(spec['HostConfig']['ReadonlyRootfs'])
            self.assertEqual({m['Target'] for m in spec['HostConfig']['Mounts']},{'/workspace','/output'})
            self.assertFalse(any('TOKEN' in value or 'KEY=' in value for value in spec['Env']))
            ledger.db.close(); assignments.close()

    def test_disposable_session_mount_requires_registered_exact_assignment(self):
        import tempfile
        from pathlib import Path
        from runner.actions import ActionLedger
        from runner.docker_config import DockerConfig
        from runner.docker_controller import DockerAssignments
        from runner.session_store import SessionStore
        class Create(Engine):
            def request(self,method,path,body=None): self.calls.append((method,path,body)); return 201,{'Id':ID}
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':'example@sha256:'+'1'*64,'network':'none','job_root':str(jobs)},root)
            store=SessionStore(root); state=store.acquire('task-1','implement','job-1',config.policy_hash,'b'*64)
            mounts=config.create_job_dirs('job-1'); engine=Create(); assignments=DockerAssignments(root+'/db',engine,config.image)
            ledger=ActionLedger(root+'/db'); ledger.approve('controller','docker_config',config.policy_hash,'operator')
            binding={'home':state['home'],'task':'task-1','role':'implement','skills':'b'*64}
            with self.assertRaises(DockerError): assignments.create_disposable('job-1',config,mounts,session={**binding,'task':'other'})
            other=DockerConfig({'socket':'/private/engine.sock','image':config.image,'network':'aw-test','job_root':str(jobs)},root)
            with self.assertRaises(DockerError): assignments.create_disposable('job-1',other,mounts,session=binding)
            with self.assertRaises(DockerError): assignments.create_disposable('job-1',config,mounts,session={**binding,'skills':'c'*64})
            self.assertEqual(assignments.create_disposable('job-1',config,mounts,session=binding),ID)
            spec=engine.calls[0][2]; mount=next(x for x in spec['HostConfig']['Mounts'] if x['Target']=='/codex-home')
            self.assertEqual(mount['Source'],state['home']); self.assertIn('CODEX_HOME=/codex-home',spec['Env'])
            self.assertFalse(any('ACCESS_TOKEN=' in item for item in spec['Env']))
            ledger.db.close(); assignments.close(); store.close()
