"""Scoped GitHub review loop using one ActionLedger and trusted workspace evidence."""
import json
import math
import re
import time
from runner.actions import ActionLedger, ActionConflict
from workflows.engineering.github import GitHub, GitHubError

class LoopError(RuntimeError): pass

class BoundLedger(ActionLedger):
    """Persist exact target and expiry in the same transaction as scoped reservation."""
    def __init__(self,path,workspace,lock_check,clock=time.time):
        super().__init__(path); self.workspace=workspace; self.lock_check=lock_check; self.clock=clock
        self.db.execute('''CREATE TABLE IF NOT EXISTS action_bindings(run TEXT NOT NULL,action TEXT NOT NULL,version TEXT NOT NULL,manifest TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(run,action,version))''')
    @staticmethod
    def encode(target):
        fields={'task','repo','branch','base','head','diff'}
        if not isinstance(target,dict) or set(target)!=fields or any(not isinstance(v,str) or not v.strip() for v in target.values()) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',target['head']) or not re.fullmatch(r'[0-9a-f]{64}',target['diff']): raise LoopError('invalid target')
        return json.dumps(target,sort_keys=True,separators=(',',':'))
    def approve_bound(self,run,action,target,actor,expires):
        manifest=self.encode(target)
        if not isinstance(actor,str) or not actor.strip() or type(expires) not in (int,float) or not math.isfinite(expires) or expires<=self.clock() or self.lock_check(run) is not True or self.workspace(run)!=target: raise LoopError('approval evidence unavailable')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM action_followups WHERE run=?',(run,)).fetchone() or self.db.execute("SELECT 1 FROM external_actions WHERE run=? AND status='uncertain'",(run,)).fetchone(): raise ActionConflict('prior action unresolved')
            row=self.db.execute('SELECT status FROM external_actions WHERE run=? AND action=? AND version=?',(run,action,target['head'])).fetchone()
            if row and row[0]!='not_executed': raise ActionConflict('approval exists')
            self.db.execute('INSERT OR REPLACE INTO external_actions VALUES(?,?,?,?,?,?)',(run,action,target['head'],actor,'approved',self.clock()))
            self.db.execute('INSERT OR REPLACE INTO action_bindings VALUES(?,?,?,?,?)',(run,action,target['head'],manifest,expires))
            self.db.execute('INSERT INTO action_authorization_history VALUES(?,?,?,?,?)',(run,action,target['head'],actor,self.clock()))
            self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
    def reserve_scoped(self,run,action,version,scope):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT manifest,expires FROM action_bindings WHERE run=? AND action=? AND version=?',(run,action,version)).fetchone()
            if not row or row[1]<=self.clock() or self.lock_check(run) is not True or self.encode(self.workspace(run))!=row[0]: raise ActionConflict('expired or stale workspace approval')
            if self.db.execute('SELECT 1 FROM action_followups WHERE run=?',(run,)).fetchone() or self.db.execute('SELECT 1 FROM action_scopes WHERE scope IN (?,?)',(scope,'run:'+run)).fetchone(): raise ActionConflict('prior operation unresolved')
            cur=self.db.execute("UPDATE external_actions SET status='uncertain',updated=? WHERE run=? AND action=? AND version=? AND status='approved'",(self.clock(),run,action,version))
            if not cur.rowcount: raise ActionConflict('exact unused approval required')
            self.db.execute('INSERT INTO action_scopes VALUES(?,?,?,?)',(scope,run,action,version)); self.db.execute('INSERT INTO action_scopes VALUES(?,?,?,?)',('run:'+run,run,action,version)); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise

class ReviewLoop:
    def __init__(self,http,owner,repo,ledger,lock_check,workspace):
        if not isinstance(ledger,BoundLedger): raise ValueError('bound ledger required')
        self.github=GitHub(http,owner,repo,ledger,lock_check); self.ledger=ledger; self.workspace=workspace; self.lock_check=lock_check
    def target(self,run):
        value=self.workspace(run); self.ledger.encode(value)
        if value['repo']!=self.github.owner+'/'+self.github.repo or self.lock_check(run) is not True: raise LoopError('workspace or claim unavailable')
        return value
    def approve_push(self,run,old,actor,expires):
        t=self.target(run); action=self.github.push_action(t['branch'],old); self.ledger.approve_bound(run,action,t,actor,expires); return action
    def push(self,run,old):
        t=self.target(run); return self.github.push(run,t['branch'],old,t['head'])
    def approve_draft(self,run,title,body,actor,expires):
        t=self.target(run); action=self.github.draft_action(t['branch'],t['base'],title,body); self.ledger.approve_bound(run,action,t,actor,expires); return action
    def draft(self,run,title,body):
        t=self.target(run); return self.github.create_draft(run,t['head'],t['branch'],t['base'],title,body)
    def review(self,run,number,dispositions):
        t=self.target(run); snapshot=self.github.review_snapshot(number,t['head']); return snapshot,self.github.triage_digest(snapshot,dispositions)
    def approve_ready(self,run,number,node,digest,actor,expires):
        t=self.target(run); action=self.github.ready_action(number,node,digest); self.ledger.approve_bound(run,action,t,actor,expires); return action
    def ready(self,run,number,node,digest,dispositions):
        t=self.target(run); return self.github.mark_ready(run,number,node,t['head'],dispositions,digest)
    def post_ready(self,run,number,node,dispositions):
        t=self.target(run); return self.github.complete_post_ready_review(run,number,node,t['head'],dispositions)
    def reconcile(self,run,action,target,scope,observer):
        """Observer independently verifies exact action, quiescence and resulting state."""
        manifest=self.ledger.encode(target)
        row=self.ledger.db.execute("SELECT b.manifest,a.status FROM action_bindings b JOIN external_actions a USING(run,action,version) WHERE b.run=? AND b.action=? AND b.version=?",(run,action,target['head'])).fetchone()
        if row!=(manifest,'uncertain') or self.lock_check(run) is not True or not callable(observer): raise LoopError('reconciliation unavailable')
        result=observer(run,action,target,scope)
        if not isinstance(result,dict) or set(result)!={'quiescent','outcome'} or result['quiescent'] is not True or result['outcome'] not in ('executed','not_executed','desired_state_observed'): raise LoopError('outcome remains uncertain')
        self.ledger.resolve_scoped(run,action,target['head'],scope,result['outcome'],followup='post_ready_review' if action.startswith('mark_ready:') and result['outcome'] in ('executed','desired_state_observed') else None)
