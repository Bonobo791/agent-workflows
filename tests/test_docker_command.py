from runner.command_supervisor import CommandError
from runner.docker_command import DockerCommandBackend
from tests.test_command_supervisor import CommandSupervisorTests

class DockerCommandTests(CommandSupervisorTests):
    def test_exact_container_observation_and_rejections(self):
        from runner.docker_controller import DockerError
        class Engine:
            def __init__(self): self.spec=None; self.state='created'; self.mode='ok'; self.cid='a'*64
            def request(self,method,path,body=None):
                if path=='/containers/create': self.spec=body; return 201,{'Id':self.cid}
                if path.endswith('/json'):
                    label=self.spec['Labels']['agent-workflows.run']
                    return 200,{'Id':self.cid,'Config':{'Labels':{'agent-workflows.run':'wrong' if self.mode=='wrong-label' else label}},'State':{'Running':self.state=='running','Status':self.state,'ExitCode':0}}
                if path.endswith('/start'): self.state='exited'; return 204,None
                if '/wait?' in path:
                    if self.mode=='timeout': raise TimeoutError()
                    return 200,{'StatusCode':1 if self.mode=='wrong-exit' else 0}
                raise AssertionError(path)
            def logs(self,cid,limit):
                if cid!=self.cid: raise DockerError('wrong id')
                return b'fixture output',b''
        engine=Engine(); self.service.docker_assignments.engine=engine
        backend=DockerCommandBackend(self.service.docker_supervisor)
        supervisor,queue,claim,snapshot=self.prepared(backend)
        self.service.approve('controller','docker_config',self.policy,'operator')
        ref=supervisor.execute('run-1',queue,claim,snapshot)
        self.assertTrue(supervisor.verify('run-1',snapshot['head'],ref,snapshot))
        self.assertEqual(engine.spec['Entrypoint']+engine.spec['Cmd'],supervisor.spec['argv']); self.assertEqual(engine.spec['WorkingDir'],'/workspace'); self.assertEqual(engine.spec['HostConfig']['NetworkMode'],'none')
        self.assertFalse(any('docker.sock' in str(item).lower() for item in engine.spec['HostConfig']['Mounts']))
        self.assertFalse(engine.spec.get('Env'))
        engine.mode='wrong-label'; self.assertFalse(supervisor.verify('run-1',snapshot['head'],ref,snapshot))
    def test_wrong_identity_and_timeout_remain_fenced(self):
        from runner.docker_controller import DockerError
        class Engine:
            def request(self,method,path,body=None):
                if path=='/containers/create': return 201,{'Id':'a'*64}
                if path.endswith('/json'): return 200,{'Id':'a'*64,'Config':{'Labels':{'agent-workflows.run':'wrong'}},'State':{'Running':False,'Status':'created','ExitCode':0}}
                raise AssertionError(path)
        self.service.docker_assignments.engine=Engine(); backend=DockerCommandBackend(self.service.docker_supervisor)
        supervisor,queue,claim,snapshot=self.prepared(backend); self.service.approve('controller','docker_config',self.policy,'operator')
        with self.assertRaises(CommandError): supervisor.execute('run-1',queue,claim,snapshot)
        self.assertEqual(self.service.graph.db.execute('SELECT state FROM supervised_commands').fetchone(),('uncertain',))
        self.assertIsNotNone(self.registry.db.execute('SELECT assignment FROM engineering_workspaces').fetchone()[0])
