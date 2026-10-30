import tempfile
import unittest
from runner.docker_controller import DockerAssignments, DockerError
from runner.docker_supervisor import DockerSupervisor

ID='a'*64
IMAGE='example@sha256:'+'1'*64
class Engine:
    def __init__(self): self.state='created'; self.calls=[]; self.label='run-1'; self.fail_wait=False
    def request(self,method,path,body=None):
        self.calls.append((method,path))
        if path=='/containers/create': return 201,{'Id':ID}
        if path.endswith('/json'): return 200,{'Id':ID,'Config':{'Labels':{'agent-workflows.run':self.label}},'State':{'Running':self.state=='running','Status':self.state}}
        if path.endswith('/start'): self.state='running'; return 204,None
        if '/stop?' in path or '/kill?' in path: self.state='exited'; return 204,None
        if '/wait?' in path:
            if self.fail_wait: raise OSError('lost')
            return 200,{'StatusCode':0}
        raise AssertionError(path)
    def attach(self,cid): raise AssertionError('not used')

class SupervisorTests(unittest.TestCase):
    def test_finish_records_verified_terminal_identity(self):
        with tempfile.TemporaryDirectory() as root:
            engine=Engine(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            assignments.create('run-1'); assignments.start('run-1')
            supervisor=DockerSupervisor(assignments)
            self.assertTrue(supervisor.finish('run-1'))
            self.assertEqual(assignments.get('run-1')['state'],'terminated')
            self.assertEqual(assignments.db.execute('SELECT container_id,status FROM docker_terminal_evidence WHERE run=?',('run-1',)).fetchone(),(ID,'exited'))
            assignments.close(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            self.assertEqual(assignments.get('run-1')['state'],'terminated')
            assignments.close()
    def test_uncertain_wait_stays_fenced_across_restart(self):
        with tempfile.TemporaryDirectory() as root:
            engine=Engine(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            assignments.create('run-1'); assignments.start('run-1'); engine.fail_wait=True
            with self.assertRaises(DockerError): DockerSupervisor(assignments).finish('run-1')
            assignments.close(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            self.assertEqual(assignments.get('run-1')['state'],'stopping')
            with self.assertRaises(DockerError): DockerSupervisor(assignments).finish('run-1')
            assignments.close()
    def test_stale_identity_cannot_be_stopped(self):
        with tempfile.TemporaryDirectory() as root:
            engine=Engine(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            assignments.create('run-1'); assignments.start('run-1'); engine.label='other'
            with self.assertRaises(DockerError): DockerSupervisor(assignments).finish('run-1')
            self.assertFalse(any('/stop?' in path for _,path in engine.calls))
            assignments.close()
    def test_explicit_kill_records_terminal_state(self):
        with tempfile.TemporaryDirectory() as root:
            engine=Engine(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            assignments.create('run-1'); assignments.start('run-1')
            self.assertTrue(DockerSupervisor(assignments).finish('run-1',kill=True))
            self.assertTrue(any('/kill?' in path for _,path in engine.calls))
            assignments.close()
    def test_malformed_wait_response_remains_fenced(self):
        class Malformed(Engine):
            def request(self,method,path,body=None):
                if '/wait?' in path: return 200,{'StatusCode':'zero'}
                return super().request(method,path,body)
        with tempfile.TemporaryDirectory() as root:
            engine=Malformed(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            assignments.create('run-1'); assignments.start('run-1')
            with self.assertRaises(DockerError): DockerSupervisor(assignments).finish('run-1')
            self.assertEqual(assignments.get('run-1')['state'],'stopping'); assignments.close()
    def test_finish_can_run_in_executor_thread(self):
        import concurrent.futures
        with tempfile.TemporaryDirectory() as root:
            engine=Engine(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            assignments.create('run-1'); assignments.start('run-1'); supervisor=DockerSupervisor(assignments)
            with concurrent.futures.ThreadPoolExecutor() as pool:
                self.assertTrue(pool.submit(supervisor.finish,'run-1').result())
            assignments.close()
    def test_finish_over_fake_engine_http(self):
        import http.server, json, os, socketserver, threading
        from runner.docker_controller import UnixEngine
        calls=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            state='created'
            def reply(self,status,data=None):
                self.send_response(status); self.end_headers()
                if data is not None: self.wfile.write(json.dumps(data).encode())
            def do_GET(self):
                calls.append(self.path)
                self.reply(200,{'Id':ID,'Config':{'Labels':{'agent-workflows.run':'run-1'}},'State':{'Running':Handler.state=='running','Status':Handler.state}})
            def do_POST(self):
                calls.append(self.path)
                length=int(self.headers.get('Content-Length','0'))
                if length: self.rfile.read(length)
                if self.path.endswith('/containers/create'): self.reply(201,{'Id':ID})
                elif self.path.endswith('/start'): Handler.state='running'; self.reply(204)
                elif '/stop?' in self.path: Handler.state='exited'; self.reply(204)
                elif '/wait?' in self.path: self.reply(200,{'StatusCode':0})
                else: self.reply(404)
            def log_message(self,*args): pass
        class Server(socketserver.UnixStreamServer): pass
        with tempfile.TemporaryDirectory() as root:
            path=os.path.join(root,'engine.sock'); server=Server(path,Handler); thread=threading.Thread(target=server.serve_forever); thread.start()
            try:
                assignment=DockerAssignments(root+'/db',UnixEngine(path),IMAGE)
                assignment.create('run-1'); assignment.start('run-1')
                self.assertTrue(DockerSupervisor(assignment).finish('run-1'))
                self.assertEqual(assignment.get('run-1')['state'],'terminated')
                assignment.close()
            finally: server.shutdown(); thread.join(); server.server_close()
        self.assertTrue(any('/stop?' in path for path in calls)); self.assertTrue(any('/wait?' in path for path in calls))
    def test_escalation_requires_exact_approval_and_quiescence(self):
        from runner.actions import ActionLedger
        with tempfile.TemporaryDirectory() as root:
            engine=Engine(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            assignments.create('run-1'); assignments.start('run-1'); engine.fail_wait=True
            supervisor=DockerSupervisor(assignments)
            with self.assertRaises(DockerError): supervisor.finish('run-1')
            ledger=ActionLedger(root+'/db'); policy='f'*64
            assignments.db.execute('INSERT INTO docker_assignment_policy VALUES(?,?)',('run-1',policy))
            version=supervisor.escalation_version('run-1',policy)
            with self.assertRaises(DockerError): supervisor.escalate('run-1',ledger,policy,lambda *_:True)
            ledger.approve('run-1','docker_kill',version,'operator')
            with self.assertRaises(DockerError): supervisor.escalate('run-1',ledger,policy,lambda *_:False)
            engine.fail_wait=False; engine.state='running'
            self.assertTrue(supervisor.escalate('run-1',ledger,policy,lambda run,cid:run=='run-1' and cid==ID))
            self.assertEqual(assignments.get('run-1')['state'],'terminated')
            self.assertTrue(any('/kill?' in path for _,path in engine.calls))
            ledger.db.close(); assignments.close()
    def test_uncertain_kill_does_not_replay(self):
        from runner.actions import ActionLedger
        class LostKill(Engine):
            def request(self,method,path,body=None):
                if '/kill?' in path: self.calls.append((method,path)); raise OSError('lost')
                return super().request(method,path,body)
        with tempfile.TemporaryDirectory() as root:
            engine=LostKill(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            assignments.create('run-1'); assignments.start('run-1'); engine.fail_wait=True
            supervisor=DockerSupervisor(assignments)
            with self.assertRaises(DockerError): supervisor.finish('run-1')
            engine.state='running'; engine.fail_wait=False
            ledger=ActionLedger(root+'/db'); policy='f'*64
            assignments.db.execute('INSERT INTO docker_assignment_policy VALUES(?,?)',('run-1',policy))
            version=supervisor.escalation_version('run-1',policy)
            ledger.approve('run-1','docker_kill',version,'operator')
            with self.assertRaises(DockerError): supervisor.escalate('run-1',ledger,policy,lambda *_:True)
            self.assertEqual(assignments.get('run-1')['state'],'escalating')
            with self.assertRaises(DockerError): supervisor.escalate('run-1',ledger,policy,lambda *_:True)
            self.assertEqual(sum('/kill?' in path for _,path in engine.calls),1)
            ledger.db.close(); assignments.close()
    def test_disposable_delayed_stop_kill_never_targets_replacement(self):
        from runner.actions import ActionLedger
        replacement='b'*64
        class Delayed(Engine):
            def request(self,method,path,body=None):
                if '/stop?' in path: self.calls.append((method,path)); raise TimeoutError('delayed')
                if path.endswith('/json'):
                    self.calls.append((method,path))
                    cid=path.split('/')[2]
                    return 200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run':'run-1' if cid==ID else 'run-2'}},'HostConfig':{'RestartPolicy':{'Name':'no'},'AutoRemove':False},'State':{'Running':self.state=='running','Status':self.state}}
                return super().request(method,path,body)
        with tempfile.TemporaryDirectory() as root:
            engine=Delayed(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            assignments.create('run-1'); assignments.start('run-1'); supervisor=DockerSupervisor(assignments)
            policy='f'*64
            assignments.db.execute('INSERT INTO docker_assignment_policy VALUES(?,?)',('run-1',policy))
            assignments.db.execute('INSERT INTO docker_disposable_assignments VALUES(?,?,?)',('run-1',ID,policy))
            with self.assertRaises(DockerError): supervisor.finish('run-1')
            assignments.db.execute("INSERT INTO docker_assignments VALUES(?,?,?,?)",('run-2',replacement,'running',IMAGE))
            ledger=ActionLedger(root+'/db'); version=supervisor.escalation_version('run-1',policy)
            ledger.approve('run-1','docker_kill',version,'operator')
            self.assertTrue(supervisor.escalate('run-1',ledger,policy,None))
            self.assertTrue(any('/kill?' in path and ID in path for _,path in engine.calls))
            self.assertFalse(any(('/stop?' in path or '/kill?' in path) and replacement in path for _,path in engine.calls))
            ledger.db.close(); assignments.close()
    def test_disposable_escalation_rejects_restartable_or_wrong_identity(self):
        from runner.actions import ActionLedger
        for mode in ('restart','wrong-id','wrong-label','missing-record'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                class Bad(Engine):
                    def request(self,method,path,body=None):
                        if '/stop?' in path: self.calls.append((method,path)); raise TimeoutError('delayed')
                        if path.endswith('/json'):
                            return 200,{'Id':'b'*64 if mode=='wrong-id' else ID,'Config':{'Labels':{'agent-workflows.run':'wrong' if mode=='wrong-label' else 'run-1'}},'HostConfig':{'RestartPolicy':{'Name':'always' if mode=='restart' else 'no'},'AutoRemove':False},'State':{'Running':True,'Status':'running'}}
                        return super().request(method,path,body)
                engine=Bad(); assignments=DockerAssignments(root+'/db',engine,IMAGE); assignments.create('run-1')
                assignments.db.execute("UPDATE docker_assignments SET state='stopping' WHERE run='run-1'")
                supervisor=DockerSupervisor(assignments); policy='f'*64
                assignments.db.execute('INSERT INTO docker_assignment_policy VALUES(?,?)',('run-1',policy))
                if mode!='missing-record': assignments.db.execute('INSERT INTO docker_disposable_assignments VALUES(?,?,?)',('run-1',ID,policy))
                ledger=ActionLedger(root+'/db'); ledger.approve('run-1','docker_kill',supervisor.escalation_version('run-1',policy),'operator')
                with self.assertRaises(DockerError): supervisor.escalate('run-1',ledger,policy,None)
                self.assertFalse(any('/kill?' in path for _,path in engine.calls))
                ledger.db.close(); assignments.close()
    def test_configured_supervisor_launches_and_finishes_unique_handle(self):
        from pathlib import Path
        from runner.actions import ActionLedger
        from runner.docker_config import DockerConfig
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(jobs)},root)
            class JobEngine(Engine):
                def request(self,method,path,body=None):
                    if path=='/containers/create': self.label=body['Labels']['agent-workflows.run']
                    return super().request(method,path,body)
            engine=JobEngine(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            supervisor=DockerSupervisor(assignments,config,lambda run:True,lambda:True)
            self.assertFalse(supervisor.preflight())
            ledger=ActionLedger(root+'/db'); ledger.approve('controller','docker_config',config.policy_hash,'operator')
            self.assertTrue(supervisor.preflight())
            handle=supervisor.start('task-1','implement')
            self.assertTrue(Path(handle.output_dir).is_dir())
            self.assertTrue(handle.finish())
            self.assertEqual(assignments.get(handle.assignment)['state'],'terminated')
            ledger.db.close(); assignments.close()
    def test_claim_lost_after_create_refuses_start(self):
        from pathlib import Path
        from runner.actions import ActionLedger
        from runner.docker_config import DockerConfig
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(jobs)},root)
            class JobEngine(Engine):
                def request(self,method,path,body=None):
                    if path=='/containers/create': self.label=body['Labels']['agent-workflows.run']
                    return super().request(method,path,body)
            engine=JobEngine(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            checks=[]
            def ready(run):
                checks.append(run)
                return run is None or checks.count('task-1')==1
            supervisor=DockerSupervisor(assignments,config,ready,lambda:True)
            ledger=ActionLedger(root+'/db'); ledger.approve('controller','docker_config',config.policy_hash,'operator')
            with self.assertRaises(DockerError): supervisor.start('task-1','implement')
            self.assertFalse(any(path.endswith('/start') for _,path in engine.calls))
            ledger.db.close(); assignments.close()
    def test_cancelled_after_start_retains_exact_running_assignment(self):
        from pathlib import Path
        from runner.actions import ActionLedger
        from runner.docker_config import DockerConfig
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(jobs)},root)
            class JobEngine(Engine):
                def request(self,method,path,body=None):
                    if path=='/containers/create': self.label=body['Labels']['agent-workflows.run']
                    return super().request(method,path,body)
            engine=JobEngine(); assignments=DockerAssignments(root+'/db',engine,IMAGE)
            def ready(run): return run is None or engine.state!='running'
            supervisor=DockerSupervisor(assignments,config,ready,lambda:True); ledger=ActionLedger(root+'/db')
            ledger.approve('controller','docker_config',config.policy_hash,'operator')
            with self.assertRaises(DockerError): supervisor.start('task-1','implement')
            row=assignments.db.execute('SELECT a.container_id,a.state FROM docker_execution_map m JOIN docker_assignments a ON a.run=m.assignment WHERE m.task_run=?',('task-1',)).fetchone()
            self.assertEqual(row,(ID,'running'))
            with self.assertRaises(DockerError): supervisor.start('task-1','implement')
            ledger.db.close(); assignments.close()
    def test_session_execution_replacement_and_fresh_reviewer(self):
        import hashlib, json
        from pathlib import Path
        from runner.actions import ActionLedger
        from runner.docker_config import DockerConfig
        from runner.session_store import SessionStore
        class JobsEngine:
            def __init__(self): self.items={}; self.specs=[]
            def request(self,method,path,body=None):
                if path=='/containers/create':
                    cid=format(len(self.items)+1,'064x'); self.specs.append(body); self.items[cid]={'state':'created','label':body['Labels']['agent-workflows.run']}; return 201,{'Id':cid}
                cid=path.split('/')[2]; item=self.items[cid]
                if path.endswith('/json'): return 200,{'Id':cid,'Config':{'Labels':{'agent-workflows.run':item['label']}},'State':{'Running':item['state']=='running','Status':item['state']}}
                if path.endswith('/start'): item['state']='running'; return 204,None
                if '/stop?' in path: item['state']='exited'; return 204,None
                if '/wait?' in path: return 200,{'StatusCode':0}
                raise AssertionError(path)
        class Delivery:
            def ready(self): return True
            def deliver(self,assignment,cid): return True
            def verify_home(self,path): return True
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(jobs)},root)
            engine=JobsEngine(); assignments=DockerAssignments(root+'/db',engine,IMAGE); store=SessionStore(root)
            supervisor=DockerSupervisor(assignments,config,lambda _:True,lambda:True,session_root=root,credential_delivery=Delivery())
            ledger=ActionLedger(root+'/db'); ledger.approve('controller','docker_config',config.policy_hash,'operator')
            skills={'writer':'abc'}; first=supervisor.start_execution('task-1','canonical_draft',None,skills,'fake-terra')
            first.bind_session('thread-1'); self.assertTrue(first.finish())
            self.assertTrue(supervisor.can_resume('task-1','canonical_draft','thread-1',skills,'fake-terra'))
            second=supervisor.start_execution('task-1','canonical_draft','thread-1',skills,'fake-terra'); second.bind_session('thread-1'); self.assertTrue(second.finish())
            reviewer=supervisor.start_execution('task-1','canonical_review',None,{'reviewer':'def'},'fake-terra'); reviewer.bind_session('review-1'); self.assertTrue(reviewer.finish())
            homes=[next(m['Source'] for m in spec['HostConfig']['Mounts'] if m['Target']=='/codex-home') for spec in engine.specs]
            self.assertEqual(homes[0],homes[1]); self.assertNotEqual(homes[1],homes[2])
            self.assertFalse(supervisor.can_resume('task-1','canonical_review','thread-1',skills,'fake-terra'))
            self.assertFalse(supervisor.can_resume('task-1','canonical_draft','thread-1',{'writer':'changed'},'fake-terra'))
            self.assertFalse(supervisor.can_resume('task-1','canonical_draft','thread-1',skills,'other-model'))
            ledger.db.close(); store.close(); assignments.close()
    def test_missing_credential_delivery_rejects_before_container_create(self):
        from pathlib import Path
        from runner.actions import ActionLedger
        from runner.docker_config import DockerConfig
        from runner.session_store import SessionStore
        from runner.adapter import CredentialDelivery
        with tempfile.TemporaryDirectory() as root:
            jobs=Path(root)/'jobs'; jobs.mkdir(mode=0o700)
            config=DockerConfig({'socket':'/private/engine.sock','image':IMAGE,'network':'none','job_root':str(jobs)},root)
            engine=Engine(); assignments=DockerAssignments(root+'/db',engine,IMAGE); store=SessionStore(root)
            supervisor=DockerSupervisor(assignments,config,lambda _:True,lambda:True,session_root=root,credential_delivery=CredentialDelivery())
            ledger=ActionLedger(root+'/db'); ledger.approve('controller','docker_config',config.policy_hash,'operator')
            with self.assertRaises(DockerError): supervisor.start_execution('task-1','implement',None,{},'fake-terra')
            self.assertFalse(any(path=='/containers/create' for _,path in engine.calls))
            ledger.db.close(); store.close(); assignments.close()
