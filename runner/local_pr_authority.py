"""Claim-scoped local GitHub push authority and independent observer."""
import json
import sqlite3
import urllib.parse
from runner.local_push import LocalPushTransport
from runner.local_mutation import LocalMutationTransport
from runner.pr_authority import ReadAndIsolatedPush
from runner.pr_observer import PRRemoteObserver
from workflows.engineering.review_loop import BoundLedger
from workflows.engineering.github import GitHubError

class LocalPRObserver:
    def __init__(self,service,http): self.service,self.http=service,http
    def observe(self,run,action,target,scope):
        try:
            kind='push' if action.startswith('push:') else 'draft' if action.startswith('open_draft:') else 'ready' if action.startswith('mark_ready:') else None
            if kind is None: raise ValueError()
            db=sqlite3.connect(self.service.state_db_path)
            try:
                ledger=db.execute('SELECT b.manifest,a.status FROM action_bindings b JOIN external_actions a USING(run,action,version) WHERE b.run=? AND b.action=? AND b.version=?',(run,action,target['head'])).fetchone()
                attempts=(db.execute("SELECT assignment FROM local_push_attempts WHERE run=? AND action=? AND scope=? AND head=? AND state IN ('reserved','terminated')",(run,action,scope,target['head'])).fetchall() if kind=='push' else db.execute("SELECT assignment FROM local_mutation_attempts WHERE run=? AND action=? AND scope=? AND kind=? AND state IN ('reserved','terminated')",(run,action,scope,kind)).fetchall())
                proposals=db.execute("SELECT proposal FROM pr_bridge_operations WHERE run=? AND kind=? AND state IN ('uncertain','observed')",(run,kind)).fetchall()
                if not ledger or ledger[0]!=BoundLedger.encode(target) or ledger[1] not in ('uncertain','executed') or len(attempts)!=1: raise ValueError()
                assignment=attempts[0][0]
                rows=db.execute('SELECT p.kind,p.unit,s.state,s.cgroup,s.invocation FROM local_pr_scopes p JOIN local_scopes s ON s.unit=p.unit AND s.run=p.run WHERE p.assignment=? AND p.run=? AND p.action=? AND p.scope=?',(assignment,run,action,scope)).fetchall()
                binding=db.execute('SELECT unit FROM engineering_local_scopes WHERE run=? AND assignment=?',(run,assignment)).fetchone() if kind=='push' else None
            finally:db.close()
            matches=[json.loads(self.service.artifacts.get(ref)) for (ref,) in proposals]
            matches=[p for p in matches if p.get('action')==action and p.get('target')==target]
            if len(matches)!=1: raise ValueError()
            proposal=matches[0]
            if kind=='push':
                if sorted(row[0] for row in rows)!=['bundle','push'] or binding!=(next(row[1] for row in rows if row[0]=='bundle'),):raise ValueError()
            elif len(rows)!=1 or rows[0][0]!=kind:raise ValueError()
            backend=self.service.model_supervisor.backend
            for _,unit,state,cgroup,invocation in rows:
                observed=backend.inspect(unit)
                if state!='terminated' or observed.get('id')!=unit or observed.get('active')!='inactive' or observed.get('load') not in ('loaded','not-found') or (observed.get('load')=='loaded' and observed.get('invocation') not in ('',invocation)) or not backend.empty_cgroup(cgroup):raise ValueError()
            prefix='/repos/'+target['repo']
            if kind=='push':
                path=prefix+'/git/ref/heads/'+urllib.parse.quote(target['branch'],safe='/')
                if self.http.request('GET',path).get('object',{}).get('sha')!=target['head']:raise ValueError()
                return {'quiescent':True,'outcome':'desired_state_observed'}
            args=proposal['args']
            if kind=='draft':
                owner=target['repo'].split('/')[0];head=urllib.parse.quote(owner+':'+target['branch'],safe='')
                items=self.http.request('GET',prefix+'/pulls?state=all&head='+head+'&per_page=100&page=1')
                if not isinstance(items,list) or len(items)!=1 or type(items[0].get('number')) is not int:raise ValueError()
                number=items[0]['number'];receipt=self.http.request('GET',prefix+'/pulls/'+str(number))
                if not PRRemoteObserver._matches_pr(receipt,target,number) or receipt.get('draft') is not True or receipt.get('title')!=args['title'] or receipt.get('body')!=args['body']:raise ValueError()
            else:
                number=args['number'];receipt=self.http.request('GET',prefix+'/pulls/'+str(number))
                if not PRRemoteObserver._matches_pr(receipt,target,number) or receipt.get('node_id')!=args['node'] or receipt.get('draft') is not False:raise ValueError()
            return {'quiescent':True,'outcome':'desired_state_observed','remote_receipt':receipt}
        except Exception:return {'quiescent':False,'outcome':'unknown'}

class LocalPRAuthority:
    def __init__(self,service,http):self.service,self.http=service,http
    def ready(self):
        try:return self.service.model_supervisor is not None and self.service.model_supervisor.preflight() is True and self.http._digest()==self.http.digest
        except Exception:return False
    def for_claim(self,run,queue_claim,graph_claim):
        if not self.ready():raise GitHubError('local PR authority unavailable')
        push=LocalPushTransport(self.service,run,queue_claim,graph_claim,self.http);mutation=LocalMutationTransport(self.service,run,queue_claim,graph_claim,self.http);observer=LocalPRObserver(self.service,self.http)
        class Bound:
            http=ReadAndIsolatedPush(self.http,push,mutation)
            def ready(bound):return True
            def supports(bound,kind):return kind in ('push','draft','ready')
            def observe(bound,*args):return observer.observe(*args)
        return Bound()
