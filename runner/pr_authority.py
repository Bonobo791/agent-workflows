"""PR action routing: push and draft/ready go through host transports; reads are read-only."""
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
