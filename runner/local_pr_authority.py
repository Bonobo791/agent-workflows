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
    """Decides the outcome from the attempt row (is it quiescent, did it succeed) and the remote (what changed)."""
    def __init__(self,service,http): self.service,self.http=service,http
    def _ref_head(self,prefix,branch):
        refs=self.http.request('GET',prefix+'/git/matching-refs/heads/'+urllib.parse.quote(branch,safe='/'))
        if not isinstance(refs,list) or len(refs)>=100: raise ValueError()
        exact=[ref for ref in refs if ref.get('ref')=='refs/heads/'+branch]
        if len(exact)>1: raise ValueError()
        return exact[0].get('object',{}).get('sha') if exact else None
    def observe(self,run,action,target,scope):
        try:
            kind='push' if action.startswith('push:') else 'draft' if action.startswith('open_draft:') else 'ready' if action.startswith('mark_ready:') else None
            if kind is None: raise ValueError()
            db=sqlite3.connect(self.service.state_db_path)
            try:
                ledger=db.execute('SELECT b.manifest,a.status FROM action_bindings b JOIN external_actions a USING(run,action,version) WHERE b.run=? AND b.action=? AND b.version=?',(run,action,target['head'])).fetchone()
                attempt=db.execute('SELECT kind,state FROM local_pr_attempts WHERE run=? AND action=? AND version=?',(run,action,target['head'])).fetchone()
                proposals=db.execute("SELECT proposal FROM pr_bridge_operations WHERE run=? AND kind=? AND state IN ('uncertain','observed')",(run,kind)).fetchall()
            finally: db.close()
            if not ledger or ledger[0]!=BoundLedger.encode(target) or ledger[1] not in ('uncertain','executed') or not attempt or attempt[0]!=kind: raise ValueError()
            # 'reserved' means the launch outcome is unknown: the attempt is not quiescent.
            if attempt[1]=='reserved': raise ValueError()
            landed=attempt[1]=='done'
            matches=[json.loads(self.service.artifacts.get(ref)) for (ref,) in proposals]
            matches=[p for p in matches if p.get('action')==action and p.get('target')==target]
            if len(matches)!=1: raise ValueError()
            args=matches[0]['args'];prefix='/repos/'+target['repo']
            if kind=='push':
                head=self._ref_head(prefix,target['branch'])
                if head==target['head']: return {'quiescent':True,'outcome':'desired_state_observed'}
                if not landed and head==args['expected_remote']: return {'quiescent':True,'outcome':'not_executed'}
                raise ValueError()
            if kind=='draft':
                owner=target['repo'].split('/')[0];head=urllib.parse.quote(owner+':'+target['branch'],safe='')
                items=self.http.request('GET',prefix+'/pulls?state=all&head='+head+'&per_page=100&page=1')
                if not isinstance(items,list) or len(items)>1: raise ValueError()
                if not items:
                    if not landed: return {'quiescent':True,'outcome':'not_executed'}
                    raise ValueError()
                if type(items[0].get('number')) is not int: raise ValueError()
                number=items[0]['number'];receipt=self.http.request('GET',prefix+'/pulls/'+str(number))
                if not PRRemoteObserver._matches_pr(receipt,target,number) or receipt.get('draft') is not True or receipt.get('title')!=args['title'] or receipt.get('body')!=args['body']: raise ValueError()
                return {'quiescent':True,'outcome':'desired_state_observed','remote_receipt':receipt}
            number=args['number'];receipt=self.http.request('GET',prefix+'/pulls/'+str(number))
            if not PRRemoteObserver._matches_pr(receipt,target,number) or receipt.get('node_id')!=args['node']: raise ValueError()
            if receipt.get('draft') is False: return {'quiescent':True,'outcome':'desired_state_observed','remote_receipt':receipt}
            if receipt.get('draft') is True and not landed: return {'quiescent':True,'outcome':'not_executed'}
            raise ValueError()
        except Exception:return {'quiescent':False,'outcome':'unknown'}

class LocalPRAuthority:
    def __init__(self,service,http):self.service,self.http=service,http
    def ready(self): return self._ready_reason() is None
    def _ready_reason(self):
        try:
            if self.service.model_supervisor is None: return 'model supervisor missing'
            if self.service.model_supervisor.preflight() is not True: return 'model supervisor preflight failed'
            if self.http._digest()!=self.http.digest: return 'http digest changed'
            return None
        except Exception as exc: return f'{type(exc).__name__}: {exc}'
    def for_claim(self,run,queue_claim,graph_claim):
        if not self.ready():raise GitHubError(f"local PR authority unavailable: {self._ready_reason() or 'state changed'}")
        push=LocalPushTransport(self.service,run,queue_claim,graph_claim,self.http);mutation=LocalMutationTransport(self.service,run,queue_claim,graph_claim,self.http);observer=LocalPRObserver(self.service,self.http)
        class Bound:
            http=ReadAndIsolatedPush(self.http,push,mutation)
            def ready(bound):return True
            def supports(bound,kind):return kind in ('push','draft','ready')
            def observe(bound,*args):return observer.observe(*args)
        return Bound()
