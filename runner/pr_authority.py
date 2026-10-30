"""Configured PR authority with isolated push and read-only remote observation.

Draft and readiness require a separately supplied scoped credential issuer.
No credentials are provisioned here.
"""
from runner.docker_push import DockerPushTransport
from runner.pr_observer import PRRemoteObserver
from runner.docker_mutation import DockerMutationTransport
from workflows.engineering.github import GitHubError

class ReadAndIsolatedPush:
    def __init__(self,read_http,push,mutation=None): self.read_http,self.push,self.mutation=read_http,push,mutation
    def request(self,method,path,body=None):
        if method=='GET' and body is None: return self.read_http.request(method,path)
        if method=='POST' and self.mutation is not None and isinstance(path,str) and path.startswith('/repos/') and path.endswith('/pulls'):
            return self.mutation.execute('draft',path[len('/repos/'):-len('/pulls')],body)
        raise GitHubError('isolated PR mutation provider unavailable')
    def push_exact(self,branch,sha): return self.push.push_exact(branch,sha)
    def mark_ready(self,node):
        if self.mutation is None: raise GitHubError('isolated readiness provider unavailable')
        repo=self.mutation.service.task_intake.config['github_owner']+'/'+self.mutation.service.task_intake.config['github_repo']
        return self.mutation.execute('ready',repo,{'node':node})

class PRRuntimeAuthority:
    def __init__(self,service,read_http,push_credentials,mutation_credentials=None):
        self.service,self.read_http,self.push_credentials,self.mutation_credentials=service,read_http,push_credentials,mutation_credentials
    def ready(self):
        supervisor=self.service.docker_supervisor
        return bool(supervisor is not None and supervisor.config is not None and supervisor.config.network!='none' and callable(getattr(self.push_credentials,'ready',None)) and self.push_credentials.ready() is True and callable(getattr(self.read_http,'request',None)))
    def for_claim(self,run,queue_claim,graph_claim):
        if not self.ready(): raise GitHubError('PR runtime authority unavailable')
        push=DockerPushTransport(self.service,run,queue_claim,graph_claim,self.push_credentials)
        observer=PRRemoteObserver(self.service,self.read_http)
        mutation=DockerMutationTransport(self.service,run,queue_claim,graph_claim,self.mutation_credentials) if self.mutation_credentials is not None and callable(getattr(self.mutation_credentials,'ready',None)) and self.mutation_credentials.ready() is True and callable(getattr(self.mutation_credentials,'issue',None)) else None
        class Bound:
            http=ReadAndIsolatedPush(self.read_http,push,mutation)
            def ready(bound): return True
            def supports(bound,kind): return kind=='push' or (kind in ('draft','ready') and mutation is not None)
            def observe(bound,*args): return observer.observe(*args)
        return Bound()
