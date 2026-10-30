import hashlib
import json
from runner.docker_config import DockerConfig
from runner.docker_push import DockerPushTransport
from runner.engineering_dispatch import workspace_snapshot
from tests.test_engineering_workspace import EngineeringWorkspaceTests
from workflows.engineering.review_loop import BoundLedger, ReviewLoop
from workflows.engineering.github import GitHubError

class DockerPushTests(EngineeringWorkspaceTests):
    def test_approved_push_uses_exact_disposable_container(self):
        from tests.test_engineering_workspace import git
        service=self.service; service.create('run-1','engineering','task'); service.enqueue('run-1')
        queue=self.control.handle('x'*40,{'action':'manual_claim'})['claim']; claim=self.control.handle('x'*40,{'action':'claim','run':'run-1'})['claim']
        old=service.docker_supervisor.config
        config=DockerConfig({'socket':old.socket,'image':old.image,'network':'aw-fixture','job_root':str(old.job_root)},str(self.root)); service.docker_supervisor.config=config
        service.approve('controller','docker_config',config.policy_hash,'operator')
        args=('run-1','owner/repo',self.base,'task-1',__import__('os').geteuid(),config.policy_hash)
        self.registry.register(*args,self.registry.binding_hash(*args))
        (self.repo/'file').write_text('changed'); git(self.repo,'add','file'); git(self.repo,'commit','-m','change')
        current=workspace_snapshot(service,'run-1')
        target={'task':'task','repo':'owner/repo','branch':'task-1','base':'main','head':current['head'],'diff':hashlib.sha256(current['diff'].encode()).hexdigest()}
        class Credentials:
            def ready(self): return True
            def deliver(self,assignment,cid): return True
        class HTTP:
            def __init__(self): self.head=self_base; self.push_transport=None
            def request(self,method,path,body=None): return {'object':{'sha':self.head}}
            def push_exact(self,branch,sha): return self.push_transport.push_exact(branch,sha)
        self_base=self.base; http=HTTP()
        class Engine:
            def __init__(self): self.spec=None; self.state='created'; self.cid='b'*64
            def request(self,method,path,body=None):
                if path=='/containers/create': self.spec=body; return 201,{'Id':self.cid}
                if path.endswith('/json'): return 200,{'Id':self.cid,'Config':{'Labels':{'agent-workflows.run':self.spec['Labels']['agent-workflows.run']}},'State':{'Running':self.state=='running','Status':self.state,'ExitCode':0}}
                if path.endswith('/start'): self.state='exited'; http.head=target['head']; return 204,None
                if '/wait?' in path: return 200,{'StatusCode':0}
                raise AssertionError(path)
        import socketserver, threading
        from runner.docker_controller import UnixEngine
        engine=Engine()
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                method,path,_=self.rfile.readline().decode().split(); headers={}
                while True:
                    line=self.rfile.readline()
                    if line==b'\r\n': break
                    key,value=line.decode().split(':',1); headers[key.lower()]=value.strip()
                length=int(headers.get('content-length','0')); body=json.loads(self.rfile.read(length)) if length else None
                status,value=engine.request(method,path.removeprefix('/v1.49'),body)
                data=json.dumps(value).encode() if value is not None else b''
                self.wfile.write(('HTTP/1.1 '+str(status)+' OK\r\nContent-Length: '+str(len(data))+'\r\nContent-Type: application/json\r\n\r\n').encode()+data); self.wfile.flush()
        class Server(socketserver.ThreadingUnixStreamServer): daemon_threads=True
        socket_path=str(self.root/'push-engine.sock'); server=Server(socket_path,Handler); thread=threading.Thread(target=server.serve_forever); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(thread.join); self.addCleanup(server.shutdown)
        service.docker_assignments.engine=UnixEngine(socket_path)
        transport=DockerPushTransport(service,'run-1',queue['token'],claim,Credentials()); http.push_transport=transport
        lock=lambda run: run=='run-1' and service.queue.may_start(run,queue['token'])
        ledger=BoundLedger(str(self.root/'state.db'),lambda run:dict(target),lock); loop=ReviewLoop(http,'owner','repo',ledger,lock,lambda run:dict(target))
        try:
            loop.approve_push('run-1',self.base,'owner',__import__('time').time()+60)
            action=loop.github.push_action('task-1',self.base)
            proposal=service.artifacts.put(json.dumps({'action':action,'target':target,'args':{'expected_remote':self.base}}).encode())
            service.graph.db.execute("INSERT INTO pr_bridge_operations VALUES(?,?,?,'uncertain',NULL)",(proposal,'run-1','push'))
            loop.push('run-1',self.base)
            self.assertIn('--force-with-lease=refs/heads/task-1:'+self.base,engine.spec['Cmd'])
            self.assertEqual(engine.spec['Entrypoint'],['git']); self.assertIn(target['head']+':refs/heads/task-1',engine.spec['Cmd'])
            self.assertEqual(engine.spec['HostConfig']['NetworkMode'],'aw-fixture'); self.assertFalse(engine.spec.get('Env'))
            self.assertFalse(any('docker.sock' in str(m).lower() for m in engine.spec['HostConfig']['Mounts']))
            self.assertEqual(ledger.db.execute("SELECT status FROM external_actions WHERE action LIKE 'push:%'").fetchone(),('executed',))
            self.assertIsNone(self.registry.db.execute('SELECT assignment FROM engineering_workspaces').fetchone()[0])
        finally: ledger.db.close()

class PushCompareAndSwapTests(EngineeringWorkspaceTests):
    def test_remote_race_and_branch_creation_use_expected_oid(self):
        import subprocess
        from tests.test_engineering_workspace import git
        from runner.docker_push import push_argv, require_fast_forward
        from runner.docker_controller import DockerError
        remote=self.root/'remote.git'; git(self.root,'init','--bare',str(remote))
        git(self.repo,'push',str(remote),self.base+':refs/heads/task-1')
        (self.repo/'file').write_text('B'); git(self.repo,'commit','-am','B'); middle=git(self.repo,'rev-parse','HEAD')
        (self.repo/'file').write_text('C'); git(self.repo,'commit','-am','C'); head=git(self.repo,'rev-parse','HEAD')
        require_fast_forward(str(self.repo),self.base,head)
        with self.assertRaises(DockerError): require_fast_forward(str(self.repo),head,self.base)
        with self.assertRaises(DockerError): require_fast_forward(str(self.repo),'0'*40,head)
        git(self.repo,'push',str(remote),middle+':refs/heads/task-1')
        cmd=push_argv('owner/repo','task-1',head,self.base); cmd[cmd.index('https://github.com/owner/repo.git')]=str(remote)
        rejected=subprocess.run(cmd,cwd=self.repo,capture_output=True)
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(git(self.root,'--git-dir',str(remote),'rev-parse','refs/heads/task-1'),middle)
        create=push_argv('owner/repo','new',head,None); create[create.index('https://github.com/owner/repo.git')]=str(remote)
        self.assertEqual(subprocess.run(create,cwd=self.repo,capture_output=True).returncode,0)
        conflicting=push_argv('owner/repo','new',middle,None); conflicting[conflicting.index('https://github.com/owner/repo.git')]=str(remote)
        self.assertNotEqual(subprocess.run(conflicting,cwd=self.repo,capture_output=True).returncode,0)
