"""Versioned DAG state for workflows; this does not execute or verify stages."""
import hashlib
import json
import sqlite3
import secrets
import time
from dataclasses import dataclass
from workflows.marketing.gates import STAGE_DEPENDENCIES, CHANNELS

class GraphError(RuntimeError):
    pass

TERMINAL = frozenset(('passed','blocked','failed','unsuitable'))
OUTCOMES = TERMINAL | {'awaiting_review','awaiting_approval','rate_limited'}

@dataclass(frozen=True)
class Definition:
    name: str
    dependencies: dict
    terminal_dependencies: frozenset = frozenset()

    def __post_init__(self):
        if not self.name or not self.dependencies: raise ValueError('empty definition')
        if not self.terminal_dependencies <= self.dependencies.keys(): raise ValueError('unknown terminal dependency stage')
        seen, visiting = set(), set()
        def visit(node):
            if node in visiting: raise ValueError('cyclic workflow')
            if node in seen: return
            visiting.add(node)
            for dep in self.dependencies[node]:
                if dep not in self.dependencies: raise ValueError('unknown dependency')
                visit(dep)
            visiting.remove(node); seen.add(node)
        for node in self.dependencies: visit(node)

    def fingerprint(self):
        data={'name':self.name, 'dependencies':self.dependencies, 'terminal_dependencies':sorted(self.terminal_dependencies)}
        return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def marketing_definition():
    return Definition('marketing', STAGE_DEPENDENCIES, frozenset({'assembly', 'asset_production'} | {channel + '_review' for channel in CHANNELS}))

def engineering_definition():
    names=('intake','plan','implement','test','qa','open_draft','triage','ready','post_ready')
    return Definition('engineering', {name: (() if i==0 else (names[i-1],)) for i,name in enumerate(names)})

class GraphStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None)
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS graph_runs(id TEXT PRIMARY KEY, workflow TEXT NOT NULL, definition_hash TEXT NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS graph_claims(task_id TEXT PRIMARY KEY, run TEXT NOT NULL, token TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS graph_stages(run TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, version TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(run,stage));
        CREATE TABLE IF NOT EXISTS graph_revisions(id INTEGER PRIMARY KEY AUTOINCREMENT);
        CREATE TABLE IF NOT EXISTS quota_pauses(scope TEXT PRIMARY KEY, run TEXT NOT NULL, stage TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS graph_sessions(run TEXT NOT NULL, role TEXT NOT NULL, session TEXT NOT NULL, PRIMARY KEY(run,role));
        CREATE TABLE IF NOT EXISTS graph_skill_state(run TEXT NOT NULL, stage TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY(run,stage));
        CREATE TABLE IF NOT EXISTS graph_attempts(run TEXT NOT NULL, stage TEXT NOT NULL, version TEXT NOT NULL, count INTEGER NOT NULL, status TEXT NOT NULL, PRIMARY KEY(run,stage,version));
        CREATE TABLE IF NOT EXISTS graph_results(run TEXT NOT NULL, stage TEXT NOT NULL, input_hash TEXT NOT NULL, output_hash TEXT NOT NULL, PRIMARY KEY(run,stage,input_hash));
        ''')
        self.definitions={}
    def close(self): self.db.close()
    def register(self, definition):
        old=self.definitions.get(definition.name)
        if old and old.fingerprint()!=definition.fingerprint(): raise GraphError('definition already registered')
        self.definitions[definition.name]=definition
    def create(self, run, workflow, task_id=None):
        definition=self.definitions.get(workflow)
        if not run or not definition: raise GraphError('unknown workflow')
        self.db.execute('INSERT INTO graph_runs VALUES(?,?,?,?,?)',(run,workflow,definition.fingerprint(),task_id or run,'active'))
    def claim(self, run, ttl):
        if ttl <= 0: raise ValueError('ttl must be positive')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT task_id FROM graph_runs WHERE id=? AND status='active'",(run,)).fetchone()
            if not row: raise GraphError('run unavailable')
            existing=self.db.execute('SELECT expires FROM graph_claims WHERE task_id=?',(row[0],)).fetchone()
            if existing and existing[0] > self.clock(): raise GraphError('task already claimed')
            token=secrets.token_urlsafe(32)
            self.db.execute('INSERT OR REPLACE INTO graph_claims VALUES(?,?,?,?)',(row[0],run,token,self.clock()+ttl))
            self.db.execute('COMMIT')
            return token
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def _lease(self,run,token):
        row=self.db.execute("SELECT c.token,c.expires FROM graph_claims c JOIN graph_runs r ON r.task_id=c.task_id WHERE r.id=? AND c.run=r.id AND r.status='active'",(run,)).fetchone()
        if not row or not token or not secrets.compare_digest(row[0],token) or row[1] <= self.clock(): raise GraphError('claim expired or invalid')
    def session(self,run,role):
        row=self.db.execute('SELECT session FROM graph_sessions WHERE run=? AND role=?',(run,role)).fetchone()
        return row[0] if row else None
    def bind_session(self,run,role,session,token):
        if not role or not session: raise ValueError('role and session required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run,token)
            old=self.session(run,role)
            if old and old!=session: raise GraphError('session already bound')
            self.db.execute('INSERT OR IGNORE INTO graph_sessions VALUES(?,?,?)',(run,role,session))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def renew(self,run,token,ttl):
        if ttl <= 0: raise ValueError('ttl must be positive')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run,token)
            self.db.execute('UPDATE graph_claims SET expires=? WHERE run=? AND token=?',(self.clock()+ttl,run,token))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def release(self,run,token):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run,token)
            self.db.execute('DELETE FROM graph_claims WHERE run=? AND token=?',(run,token))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def cancel(self,run,token):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run,token)
            self.db.execute("UPDATE graph_runs SET status='cancelled' WHERE id=?",(run,))
            self.db.execute('DELETE FROM graph_claims WHERE run=? AND token=?',(run,token))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def _definition(self,run):
        row=self.db.execute('SELECT workflow,definition_hash FROM graph_runs WHERE id=?',(run,)).fetchone()
        if not row or row[0] not in self.definitions: raise GraphError('run or workflow unavailable')
        definition=self.definitions[row[0]]
        if row[1]!=definition.fingerprint(): raise GraphError('workflow definition changed; migration required')
        return definition
    def _snapshot(self, run, stage):
        definition=self._definition(run)
        if stage not in definition.dependencies: raise GraphError('unknown stage')
        rows={r[0]: (r[1],r[2],r[3]) for r in self.db.execute('SELECT stage,status,version,revision FROM graph_stages WHERE run=?',(run,))}
        deps={}
        for dep in definition.dependencies[stage]:
            value=rows.get(dep)
            allowed=TERMINAL if stage in definition.terminal_dependencies else {'passed'}
            if not value or value[0] not in allowed: raise GraphError('dependency not satisfied')
            deps[dep]=value[2]
        return {'dependencies':deps, 'current':rows.get(stage, (None,None,None))[2]}
    def begin_attempt(self,run,stage,version,token,max_attempts,correction_id=None):
        if not isinstance(version,str) or not version or type(max_attempts) is not int or max_attempts < 1: raise ValueError('invalid attempt')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run,token)
            self._snapshot(run,stage)
            current=self.get(run,stage)
            if current and current['status']=='passed' and current['version']==version: raise GraphError('stage already passed')
            if self.db.execute("SELECT 1 FROM graph_attempts WHERE run=? AND stage=? AND status='uncertain'",(run,stage)).fetchone(): raise GraphError('prior attempt unresolved')
            if correction_id is not None:
                correction=self.db.execute("SELECT writer_version,review_stage,review_version,state FROM correction_attempts WHERE id=? AND run=? AND writer_stage=?",(correction_id,run,stage)).fetchone()
                if not correction or correction[3]!='reserved': raise GraphError('correction reservation unavailable')
                writer=self.get(run,stage); review=self.get(run,correction[1])
                if not writer or writer['status']!='passed' or writer['version']!=correction[0] or not review or review['status']!='blocked' or review['version']!=correction[2]: raise GraphError('correction evidence changed')
            row=self.db.execute('SELECT count FROM graph_attempts WHERE run=? AND stage=? AND version=?',(run,stage,version)).fetchone()
            count=(row[0] if row else 0)+1
            if count>max_attempts: raise GraphError('attempt limit reached')
            self.db.execute('INSERT OR REPLACE INTO graph_attempts VALUES(?,?,?,?,?)',(run,stage,version,count,'uncertain'))
            if correction_id is not None:
                self.db.execute("UPDATE correction_attempts SET state='running',input_hash=? WHERE id=? AND state='reserved'",(version,correction_id))
            self.db.execute('COMMIT')
            return count
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def resolve_attempt(self,run,stage,version,token,outcome):
        if outcome not in ('failed','completed','not_executed'): raise ValueError('confirmed outcome required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run,token)
            cur=self.db.execute("UPDATE graph_attempts SET status=? WHERE run=? AND stage=? AND version=? AND status='uncertain'",(outcome,run,stage,version))
            if not cur.rowcount: raise GraphError('attempt unavailable')
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def prepare(self, run, stage, token):
        self._lease(run,token)
        return self._snapshot(run,stage)
    def invalidate(self,run,stage,snapshot,token):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run,token)
            definition=self._definition(run)
            if self._snapshot(run,stage)!=snapshot: raise GraphError('stale stage inputs')
            if self.db.execute("SELECT 1 FROM graph_attempts WHERE run=? AND stage=? AND status='uncertain'",(run,stage)).fetchone(): raise GraphError('prior attempt unresolved')
            descendants={stage}; pending=[stage]
            while pending:
                parent=pending.pop()
                for child,deps in definition.dependencies.items():
                    if parent in deps and child not in descendants: descendants.add(child); pending.append(child)
            for child in descendants: self.db.execute('DELETE FROM graph_stages WHERE run=? AND stage=?',(run,child))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def finish(self, run, stage, snapshot, status, version, token, completion_input=None, skill_fingerprint=None, session_record=None, quota_scope=None, correction_id=None, pr_reconcile=None):
        if status not in OUTCOMES or not isinstance(version,str) or not version: raise GraphError('invalid outcome')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run,token)
            definition=self._definition(run)
            if self._snapshot(run,stage)!=snapshot: raise GraphError('stale stage inputs')
            if stage != 'assembly' and status in ('passed', 'awaiting_review'):
                for dep in definition.dependencies[stage]:
                    if self.get(run,dep)['status'] != 'passed': raise GraphError('cannot pass with a blocked dependency')
            current=self.get(run,stage)
            prior_skill=self.db.execute('SELECT fingerprint FROM graph_skill_state WHERE run=? AND stage=?',(run,stage)).fetchone()
            same_skill=skill_fingerprint is None or (prior_skill and prior_skill[0]==skill_fingerprint)
            if current and current['status']==status and current['version']==version and same_skill and completion_input is None and pr_reconcile is None:
                self.db.execute('COMMIT'); return
            descendants=set(); pending=[stage]
            while pending:
                parent=pending.pop()
                for child,deps in definition.dependencies.items():
                    if parent in deps and child not in descendants: descendants.add(child); pending.append(child)
            for child in descendants: self.db.execute('DELETE FROM graph_stages WHERE run=? AND stage=?',(run,child))
            revision=self.db.execute('INSERT INTO graph_revisions DEFAULT VALUES').lastrowid
            self.db.execute('INSERT OR REPLACE INTO graph_stages VALUES(?,?,?,?,?)',(run,stage,status,version,revision))
            if completion_input is not None:
                if status not in ('passed','blocked','unsuitable','failed','rate_limited','awaiting_review'): raise GraphError('invalid completion')
                cur=self.db.execute("UPDATE graph_attempts SET status='completed' WHERE run=? AND stage=? AND version=? AND status='uncertain'",(run,stage,completion_input))
                if not cur.rowcount: raise GraphError('attempt unavailable')
                if status=='rate_limited':
                    self.db.execute('UPDATE graph_attempts SET count=count-1 WHERE run=? AND stage=? AND version=?',(run,stage,completion_input))
                    if quota_scope: self.db.execute('INSERT OR REPLACE INTO quota_pauses VALUES(?,?,?)',(quota_scope,run,stage))
                self.db.execute('INSERT OR REPLACE INTO graph_results VALUES(?,?,?,?)',(run,stage,completion_input,version))
                if session_record is not None:
                    existing=self.session(run,stage)
                    if existing and existing != session_record: raise GraphError('session already bound')
                    self.db.execute('INSERT OR IGNORE INTO graph_sessions VALUES(?,?,?)',(run,stage,session_record))
            if skill_fingerprint is not None: self.db.execute('INSERT OR REPLACE INTO graph_skill_state VALUES(?,?,?)',(run,stage,skill_fingerprint))
            if correction_id is not None:
                if status!='passed' or self.db.execute("UPDATE correction_attempts SET state='completed',result_version=? WHERE id=? AND run=? AND writer_stage=? AND input_hash=? AND state='running'",(version,correction_id,run,stage,completion_input)).rowcount!=1: raise GraphError('correction completion mismatch')
            if pr_reconcile is not None:
                if stage not in ('open_draft','ready') or status!='passed' or not isinstance(pr_reconcile,dict) or set(pr_reconcile)!={'proposal','action','head','scope','result_ref','kind'} or pr_reconcile['kind']!=('draft' if stage=='open_draft' else 'ready'): raise GraphError('invalid PR reconciliation')
                item=pr_reconcile
                scoped=self.db.execute('SELECT run,action,version FROM action_scopes WHERE scope=?',(item['scope'],)).fetchone()
                if scoped!=(run,item['action'],item['head']): raise GraphError('PR action scope changed')
                if self.db.execute("UPDATE external_actions SET status='desired_state_observed',updated=? WHERE run=? AND action=? AND version=? AND status='uncertain'",(time.time(),run,item['action'],item['head'])).rowcount!=1: raise GraphError('PR action unavailable')
                if self.db.execute("UPDATE pr_bridge_operations SET state='desired_state_observed',result=? WHERE proposal=? AND run=? AND kind=? AND state='uncertain'",(item['result_ref'],item['proposal'],run,item['kind'])).rowcount!=1: raise GraphError('PR proposal unavailable')
                if item['kind']=='ready': self.db.execute('INSERT OR IGNORE INTO action_followups(run,action,version,kind,target) VALUES(?,?,?,?,?)',(run,item['action'],item['head'],'post_ready_review',item['scope']))
                self.db.execute('DELETE FROM action_scopes WHERE scope IN (?,?)',(item['scope'],'run:'+run))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def quota_paused(self,scope):
        return bool(self.db.execute('SELECT 1 FROM quota_pauses WHERE scope=?',(scope,)).fetchone())
    def resume_quota(self,scope):
        self.db.execute('DELETE FROM quota_pauses WHERE scope=?',(scope,))
    def resume_rate_limited(self,run,stage,token):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run,token)
            cur=self.db.execute("DELETE FROM graph_stages WHERE run=? AND stage=? AND status='rate_limited'",(run,stage))
            if not cur.rowcount: raise GraphError('stage is not rate limited')
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def get(self,run,stage):
        row=self.db.execute('SELECT status,version,revision FROM graph_stages WHERE run=? AND stage=?',(run,stage)).fetchone()
        return dict(zip(('status','version','revision'),row)) if row else None
