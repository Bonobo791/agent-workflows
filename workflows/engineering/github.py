"""GitHub REST contract over an injected authenticated HTTP transport.

No transport or credentials are configured by this module. Comments are data,
not instructions. External writes require an exact target and commit approval.
"""
import re
import hashlib
import json
import urllib.parse
from runner.actions import ActionConflict

class GitHubError(RuntimeError): pass

class GitHub:
    def __init__(self,http,owner,repo,ledger=None,lock_check=None):
        if not all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_.-]+',v) for v in (owner,repo)): raise ValueError('invalid repository')
        self.http,self.owner,self.repo,self.ledger,self.lock_check=http,owner,repo,ledger,lock_check
        self.prefix=f'/repos/{owner}/{repo}'
    @staticmethod
    def _sha(sha):
        if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{40,64}',sha): raise GitHubError('full commit ID required')
        return sha
    @staticmethod
    def _branch(branch):
        if not isinstance(branch,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}',branch) or '..' in branch or '//' in branch or branch.endswith(('/', '.', '.lock')): raise GitHubError('invalid branch')
        return branch
    def draft_action(self,head,base,title,body):
        if not isinstance(title,str) or not title or not isinstance(body,str): raise GitHubError('invalid PR content')
        data=json.dumps({'owner':self.owner,'repo':self.repo,'head':self._branch(head),'base':self._branch(base),'title':title,'body':body},sort_keys=True,separators=(',',':')).encode()
        return 'open_draft:'+hashlib.sha256(data).hexdigest()
    def _pages(self,path,key=None):
        result=[]
        for page in range(1,101):
            data=self.http.request('GET',f'{path}?per_page=100&page={page}')
            items=data.get(key) if key and isinstance(data,dict) else data
            if not isinstance(items,list): raise GitHubError('incomplete paginated response')
            result.extend(items)
            if len(items)<100: return result
        raise GitHubError('pagination limit reached')
    def review_snapshot(self,number,sha):
        self._sha(sha)
        if type(number) is not int or number<1: raise GitHubError('invalid pull request')
        path=self.prefix+'/pulls/'+str(number)
        def head():
            value=self.http.request('GET',path)
            try: return value['head']['sha']
            except (KeyError,TypeError): raise GitHubError('missing PR head') from None
        if head()!=sha: raise GitHubError('PR head changed')
        checks=self._pages(self.prefix+'/commits/'+sha+'/check-runs','check_runs')
        status=self.http.request('GET',self.prefix+'/commits/'+sha+'/status')
        if not isinstance(status,dict) or status.get('state')!='success': raise GitHubError('commit statuses incomplete')
        review=self._pages(path+'/comments')
        issue=self._pages(self.prefix+'/issues/'+str(number)+'/comments')
        reviews=self._pages(path+'/reviews')
        if head()!=sha: raise GitHubError('PR head changed')
        outstanding={}
        decisions=[]
        for review_item in reviews:
            try:
                author=review_item['user']['login']; state=review_item['state']; when=review_item['submitted_at']; commit=review_item['commit_id']
            except (KeyError,TypeError): raise GitHubError('review decision incomplete') from None
            if not isinstance(author,str) or not author or not isinstance(when,str) or not when or state not in ('APPROVED','CHANGES_REQUESTED','COMMENTED','DISMISSED'): raise GitHubError('review decision incomplete')
            decisions.append({'author':author,'state':state,'submitted_at':when,'commit':commit})
        for decision in sorted(decisions,key=lambda item:item['submitted_at']):
            if decision['state']=='CHANGES_REQUESTED': outstanding[decision['author']]=True
            elif decision['state'] in ('APPROVED','DISMISSED'): outstanding.pop(decision['author'],None)
        normalized=[]
        for check in checks:
            if not isinstance(check,dict) or check.get('head_sha')!=sha: raise GitHubError('check commit mismatch')
            normalized.append({'sha':sha,'status':'success' if check.get('status')=='completed' and check.get('conclusion')=='success' else 'pending'})
        normalized.append({'sha':sha,'status':'success','source':'commit_status'})
        comments=[]
        for kind,items in (('review',review),('issue',issue),('review_decision',reviews)):
            for item in items:
                if not isinstance(item,dict) or type(item.get('id')) is not int: raise GitHubError('invalid comment')
                comments.append({'id':kind+':'+str(item['id']),'body':item.get('body','')})
        return {'head':sha,'checks':normalized,'comments':comments,'review_comments':review,'issue_comments':issue,'review_decisions':decisions,'unresolved_change_requests':sorted(outstanding)} 
    def create_draft(self,run,sha,head,base,title,body):
        self._sha(sha); action=self.draft_action(head,base,title,body)
        scope=f'github:{self.owner}/{self.repo}:branch:{head}'
        if not self.ledger or not self.lock_check: raise GitHubError('task lock and approval required')
        if self.lock_check(run) is not True: raise GitHubError('task claim unavailable')
        try: self.ledger.reserve_scoped(run,action,sha,scope)
        except ActionConflict: raise GitHubError('exact unused approval required') from None
        try:
            branch=self.http.request('GET',self.prefix+'/git/ref/heads/'+urllib.parse.quote(head,safe='/'))
            if branch.get('object',{}).get('sha')!=sha or self.lock_check(run) is not True: raise GitHubError('branch or task claim changed')
        except Exception:
            self.ledger.resolve_scoped(run,action,sha,scope,'not_executed')
            raise GitHubError('precondition failed; approval invalidated') from None
        try:
            result=self.http.request('POST',self.prefix+'/pulls',{'head':head,'base':base,'title':title,'body':body,'draft':True})
            if not isinstance(result,dict) or result.get('draft') is not True or not result.get('number') or result.get('head',{}).get('sha')!=sha: raise GitHubError('unexpected PR response')
            verified=self.http.request('GET',self.prefix+'/pulls/'+str(result['number']))
            branch=self.http.request('GET',self.prefix+'/git/ref/heads/'+urllib.parse.quote(head,safe='/'))
            if (verified.get('number')!=result['number'] or verified.get('draft') is not True or verified.get('state')!='open' or verified.get('title')!=title or verified.get('body')!=body
                or verified.get('head',{}).get('sha')!=sha or verified.get('head',{}).get('ref')!=head
                or verified.get('head',{}).get('repo',{}).get('full_name')!=self.owner+'/'+self.repo
                or verified.get('base',{}).get('ref')!=base or verified.get('base',{}).get('repo',{}).get('full_name')!=self.owner+'/'+self.repo
                or branch.get('object',{}).get('sha')!=sha or self.lock_check(run) is not True): raise GitHubError('postcondition failed')
            self.ledger.resolve_scoped(run,action,sha,scope,'executed')
            return verified
        except Exception: raise GitHubError('creation outcome uncertain; inspect before retry') from None

    def push_action(self,branch,expected_remote):
        self._branch(branch)
        if expected_remote is not None: self._sha(expected_remote)
        payload=json.dumps({'owner':self.owner,'repo':self.repo,'branch':branch,'expected_remote':expected_remote},sort_keys=True,separators=(',',':')).encode()
        return 'push:'+hashlib.sha256(payload).hexdigest()
    def push(self,run,branch,expected_remote,sha):
        self._sha(sha); action=self.push_action(branch,expected_remote)
        scope=f'github:{self.owner}/{self.repo}:branch:{branch}'
        if not self.ledger or not self.lock_check or self.lock_check(run) is not True: raise GitHubError('task lock and approval required')
        try: self.ledger.reserve_scoped(run,action,sha,scope)
        except ActionConflict: raise GitHubError('exact unused approval required') from None
        path=self.prefix+'/git/ref/heads/'+urllib.parse.quote(branch,safe='/')
        matching=self.prefix+'/git/matching-refs/heads/'+urllib.parse.quote(branch,safe='/')
        def current_head():
            if expected_remote is not None:
                return self.http.request('GET',path).get('object',{}).get('sha')
            refs=self.http.request('GET',matching)
            if not isinstance(refs,list) or len(refs)>=100: raise GitHubError('branch absence not confirmed')
            exact=[ref for ref in refs if ref.get('ref')=='refs/heads/'+branch]
            if len(exact)>1: raise GitHubError('ambiguous branch')
            if not exact: return None
            value=exact[0].get('object',{}).get('sha')
            self._sha(value)
            return value
        try:
            before=current_head()
            if before!=expected_remote or self.lock_check(run) is not True: raise GitHubError('branch changed')
        except Exception:
            self.ledger.resolve_scoped(run,action,sha,scope,'not_executed')
            raise GitHubError('precondition failed; approval invalidated') from None
        try:
            self.http.push_exact(branch,sha)
            after=current_head()
            if after!=sha or self.lock_check(run) is not True: raise GitHubError('postcondition failed')
            self.ledger.resolve_scoped(run,action,sha,scope,'executed')
        except Exception: raise GitHubError('push outcome uncertain; inspect before retry') from None
    def triage_digest(self,snapshot,dispositions):
        from workflows.engineering.triage import review_snapshot
        if not isinstance(dispositions,dict): raise GitHubError('triage evidence required')
        classifications={}
        for key,value in dispositions.items():
            if not isinstance(value,dict) or not isinstance(value.get('evidence'),str) or not value['evidence'].strip() or value.get('disposition') not in ('valid','invalid','duplicate','obsolete','out_of_scope'): raise GitHubError('invalid triage evidence')
            classifications[key]=value['disposition']
        try:
            if not review_snapshot(snapshot,snapshot['head'],classifications): raise GitHubError('review or checks not ready')
        except (KeyError,ValueError): raise GitHubError('triage incomplete') from None
        return hashlib.sha256(json.dumps({'snapshot':snapshot,'dispositions':dispositions},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def ready_action(self,number,node_id,triage_hash):
        if type(number) is not int or number<1 or not isinstance(node_id,str) or not node_id or not isinstance(triage_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',triage_hash): raise GitHubError('invalid PR identity')
        payload=json.dumps({'owner':self.owner,'repo':self.repo,'number':number,'node_id':node_id,'triage_hash':triage_hash},sort_keys=True,separators=(',',':')).encode()
        return 'mark_ready:'+hashlib.sha256(payload).hexdigest()
    def mark_ready(self,run,number,node_id,sha,dispositions,triage_hash):
        self._sha(sha); action=self.ready_action(number,node_id,triage_hash)
        scope=f'github:{self.owner}/{self.repo}:pr:{number}'
        if not self.ledger or not self.lock_check or self.lock_check(run) is not True: raise GitHubError('task lock and approval required')
        try: self.ledger.reserve_scoped(run,action,sha,scope)
        except ActionConflict: raise GitHubError('exact unused approval required') from None
        path=self.prefix+'/pulls/'+str(number)
        try:
            snapshot=self.review_snapshot(number,sha)
            if self.triage_digest(snapshot,dispositions)!=triage_hash: raise GitHubError('triage changed')
            before=self.http.request('GET',path)
            if before.get('node_id')!=node_id or before.get('draft') is not True or before.get('head',{}).get('sha')!=sha or self.lock_check(run) is not True: raise GitHubError('PR changed')
        except Exception:
            self.ledger.resolve_scoped(run,action,sha,scope,'not_executed')
            raise GitHubError('precondition failed; approval invalidated') from None
        try:
            result=self.http.mark_ready(node_id)
            after=self.http.request('GET',path)
            if result.get('id')!=node_id or result.get('isDraft') is not False or after.get('node_id')!=node_id or after.get('draft') is not False or after.get('head',{}).get('sha')!=sha or self.lock_check(run) is not True: raise GitHubError('postcondition failed')
            self.ledger.resolve_scoped(run,action,sha,scope,'executed',followup='post_ready_review')
            return {'pull_request':after,'post_ready_review_required':True}
        except Exception: raise GitHubError('readiness outcome uncertain; inspect before retry') from None

    def complete_post_ready_review(self,run,number,node_id,sha,dispositions):
        if not self.ledger or not self.lock_check or self.lock_check(run) is not True: raise GitHubError('task claim unavailable')
        snapshot=self.review_snapshot(number,sha)
        digest=self.triage_digest(snapshot,dispositions)
        after=self.http.request('GET',self.prefix+'/pulls/'+str(number))
        if after.get('node_id')!=node_id or after.get('draft') is not False or after.get('head',{}).get('sha')!=sha: raise GitHubError('PR changed during review')
        try: self.ledger.complete_followup(run,sha,'post_ready_review',f'github:{self.owner}/{self.repo}:pr:{number}',digest,self.lock_check)
        except ActionConflict: raise GitHubError('post-ready review not recorded') from None
        return digest

class HTTPSGitHub:
    """Fixed-origin REST transport; construction makes no network request."""
    def __init__(self,token,push_transport=None):
        self.push_transport=push_transport
        import urllib.request
        from runner.oauth_transport import _NoRedirect
        if not isinstance(token,str) or not token or '\n' in token or '\r' in token: raise GitHubError('credential required')
        self.token=token
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    def request(self,method,path,body=None):
        import urllib.request
        if method not in ('GET','POST') or not isinstance(path,str) or not path.startswith('/repos/') or path.startswith('//') or '\r' in path or '\n' in path or '#' in path: raise GitHubError('unsupported GitHub request')
        try:
            data=json.dumps(body,separators=(',',':')).encode() if body is not None else None
            request=urllib.request.Request('https://api.github.com'+path,data=data,method=method,headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'agent-workflows','Content-Type':'application/json'})
            with self.opener.open(request,timeout=30) as response:
                payload=response.read(2097153)
                if len(payload)>2097152: raise GitHubError('response too large')
                return json.loads(payload)
        except Exception: raise GitHubError('GitHub request failed; inspect outcome before retrying writes') from None


    def push_exact(self,branch,sha):
        if self.push_transport is None: raise GitHubError('isolated push supervisor unavailable')
        return self.push_transport.push_exact(branch,sha)

    def mark_ready(self,node_id):
        import urllib.request
        if not isinstance(node_id,str) or not node_id: raise GitHubError('invalid PR identity')
        query='mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { pullRequest { id isDraft } } }'
        try:
            data=json.dumps({'query':query,'variables':{'id':node_id}},separators=(',',':')).encode()
            request=urllib.request.Request('https://api.github.com/graphql',data=data,method='POST',headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','User-Agent':'agent-workflows','Content-Type':'application/json'})
            with self.opener.open(request,timeout=30) as response:
                payload=response.read(2097153)
                if len(payload)>2097152: raise GitHubError('response too large')
                result=json.loads(payload)
                if result.get('errors'): raise GitHubError('GraphQL mutation failed')
                return result['data']['markPullRequestReadyForReview']['pullRequest']
        except Exception: raise GitHubError('GitHub mutation failed; inspect outcome before retry') from None

class GitPushTransport:
    """Non-force exact-commit push via a separately isolated supervisor."""
    def __init__(self,worktree,owner,repo,supervisor,environment):
        if not all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_.-]+',v) for v in (owner,repo)): raise GitHubError('invalid repository')
        self.worktree,self.url,self.supervisor,self.environment=worktree,f'https://github.com/{owner}/{repo}.git',supervisor,dict(environment)
    def push_exact(self,branch,sha):
        GitHub._branch(branch); GitHub._sha(sha)
        env={k:v for k,v in self.environment.items() if k in ('PATH','HOME','LANG','TMPDIR','GIT_CONFIG_GLOBAL')}
        env['GIT_TERMINAL_PROMPT']='0'
        env['GIT_CONFIG_NOSYSTEM']='1'
        env['GIT_CONFIG_GLOBAL']='/dev/null'
        if self.supervisor.verify_isolated_git_context(self.worktree,self.url) is not True: raise GitHubError('isolated Git configuration unavailable')
        args=['git','-c','core.hooksPath=/dev/null','push','--porcelain','--',self.url,sha+':refs/heads/'+branch]
        if self.supervisor.run(args,cwd=self.worktree,env=env)!=0: raise GitHubError('push failed; outcome requires inspection')
