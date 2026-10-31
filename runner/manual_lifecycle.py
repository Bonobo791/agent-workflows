"""Manual, operator-attested worker lifecycle; no machine observation is implied."""
import hashlib
import json
import secrets
import sqlite3
import time

class ManualLifecycleError(RuntimeError): pass

class ManualLifecycle:
    auto = False
    def __init__(self, path, target, incarnation):
        if any(not isinstance(v,str) or not v.strip() or len(v)>200 for v in (target,incarnation)):
            raise ManualLifecycleError('manual target and incarnation required')
        self.target={'manual_target':target}; self.incarnation=incarnation
        self.policy_hash=hashlib.sha256(json.dumps(self.target,sort_keys=True).encode()).hexdigest()
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS manual_workers(
            target TEXT PRIMARY KEY, incarnation TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manual_assignments(
            run TEXT PRIMARY KEY, target TEXT NOT NULL, incarnation TEXT NOT NULL,
            state TEXT NOT NULL, attestation TEXT, UNIQUE(target,incarnation));
            CREATE TABLE IF NOT EXISTS manual_incarnation_history(target TEXT NOT NULL, incarnation TEXT NOT NULL, PRIMARY KEY(target,incarnation));
            CREATE TABLE IF NOT EXISTS checkpoint_replacements(id INTEGER PRIMARY KEY,run TEXT NOT NULL,target TEXT NOT NULL,old_incarnation TEXT NOT NULL,new_incarnation TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS manual_lifecycle_audit(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, run TEXT NOT NULL, state TEXT NOT NULL, confirmation TEXT, created REAL NOT NULL);
            CREATE TRIGGER IF NOT EXISTS manual_lifecycle_audit_insert AFTER INSERT ON manual_assignments BEGIN
                INSERT INTO manual_lifecycle_audit(schema_version,run,state,confirmation,created) VALUES(1,NEW.run,NEW.state,NULL,(julianday('now')-2440587.5)*86400.0);
            END;
            CREATE TRIGGER IF NOT EXISTS manual_lifecycle_audit_update AFTER UPDATE OF state ON manual_assignments WHEN NEW.state != OLD.state BEGIN
                INSERT INTO manual_lifecycle_audit(schema_version,run,state,confirmation,created) VALUES(1,NEW.run,NEW.state,CASE WHEN NEW.state='stopped' AND NEW.attestation='terminated_and_fenced' THEN 'operator-attested' ELSE NULL END,(julianday('now')-2440587.5)*86400.0);
            END;''')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT OR IGNORE INTO manual_incarnation_history SELECT target,incarnation FROM manual_workers')
            self.db.execute('INSERT OR IGNORE INTO manual_incarnation_history SELECT target,incarnation FROM manual_assignments')
            row=self.db.execute('SELECT incarnation,state FROM manual_workers WHERE target=?',(target,)).fetchone()
            if row and row[0]!=incarnation: raise ManualLifecycleError('configured incarnation differs from persisted identity')
            if not row:
                self.db.execute('INSERT INTO manual_workers VALUES(?,?,?)',(target,incarnation,'available'))
                self.db.execute('INSERT OR IGNORE INTO manual_incarnation_history VALUES(?,?)',(target,incarnation))
            self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); self.db.close(); raise
    def close(self): self.db.close()
    def claim(self,ttl=60):
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            target=self.target['manual_target']
            row=db.execute('SELECT incarnation,state FROM manual_workers WHERE target=?',(target,)).fetchone()
            if row!=(self.incarnation,'available'): raise ManualLifecycleError('worker unavailable')
            db.execute("UPDATE jobs SET status='uncertain' WHERE status IN ('running','cancelling') AND expires<=?",(time.time(),))
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('running','cancelling','uncertain')").fetchone(): raise ManualLifecycleError('another job is active or uncertain')
            job=db.execute("SELECT run FROM jobs WHERE status='queued' ORDER BY seq LIMIT 1").fetchone()
            if not job: db.execute('COMMIT'); return None
            token=secrets.token_urlsafe(32)
            prior=db.execute('SELECT target,incarnation,state FROM manual_assignments WHERE run=?',(job[0],)).fetchone()
            if prior is None: db.execute("INSERT INTO manual_assignments(run,target,incarnation,state) VALUES(?,?,?,'running')",(job[0],target,self.incarnation))
            elif prior==(target,self.incarnation,'checkpointed'): db.execute("UPDATE manual_assignments SET state='running' WHERE run=? AND state='checkpointed'",(job[0],))
            else: raise ManualLifecycleError('prior assignment unavailable')
            db.execute("UPDATE manual_workers SET state='assigned' WHERE target=?",(target,))
            db.execute("UPDATE jobs SET status='running',worker=?,token=?,expires=? WHERE run=?",(self.incarnation,token,time.time()+ttl,job[0]))
            db.execute('COMMIT'); return {'run':job[0],'token':token,'incarnation':self.incarnation}
        except Exception: db.execute('ROLLBACK'); raise
    def status(self,run):
        row=self.db.execute('SELECT incarnation,state FROM manual_assignments WHERE run=? AND target=?',(run,self.target['manual_target'])).fetchone()
        if not row: raise ManualLifecycleError('assignment unavailable')
        return {'incarnation':row[0],'status':row[1],'confirmation':'operator-attested' if row[1]=='stopped' else None}
    def manual_stop(self,run):
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            state=self.status(run)
            if state['incarnation']!=self.incarnation or state['status'] not in ('running','pending'): raise ManualLifecycleError('stale or stopped assignment')
            db.execute("UPDATE manual_assignments SET state='pending' WHERE run=?",(run,)); db.execute('COMMIT')
            return {'target':self.target['manual_target'],'incarnation':self.incarnation,'evidence_required':'Operator must stop this exact incarnation and confirm termination and restart fencing; confirmation is operator-attested, not machine-observed.'}
        except Exception: db.execute('ROLLBACK'); raise
    def confirm_operator_stop(self,run,incarnation,attestation):
        if incarnation!=self.incarnation or attestation!='terminated_and_fenced': raise ManualLifecycleError('exact incarnation and explicit attestation required')
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            state=self.status(run)
            worker=db.execute('SELECT incarnation,state FROM manual_workers WHERE target=?',(self.target['manual_target'],)).fetchone()
            job=db.execute('SELECT status FROM jobs WHERE run=?',(run,)).fetchone()
            if state['incarnation']!=incarnation or state['status']!='pending' or worker!=(incarnation,'assigned') or not job or job[0] not in ('cancelling','uncertain'): raise ManualLifecycleError('confirmation stale, duplicate, or conflicting')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docker_execution_map'").fetchone():
                unresolved=db.execute("SELECT 1 FROM docker_execution_map m LEFT JOIN docker_assignments a ON a.run=m.assignment LEFT JOIN docker_terminal_evidence e ON e.run=a.run AND e.container_id=a.container_id WHERE m.task_run=? AND (a.run IS NULL OR a.state IS NOT 'terminated' OR e.run IS NULL) LIMIT 1",(run,)).fetchone()
                if unresolved: raise ManualLifecycleError('Docker termination or launch remains unresolved')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_scopes'").fetchone():
                from runner.local_scope import unresolved_scope_rows
                columns={row[1] for row in db.execute('PRAGMA table_info(local_scopes)')}
                if not {'run','unit','invocation','cgroup','state'}<=columns: raise ManualLifecycleError('local scope termination or launch remains unresolved')
                if unresolved_scope_rows(db,run): raise ManualLifecycleError('local scope termination or launch remains unresolved')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='engineering_workspaces'").fetchone() and db.execute('SELECT 1 FROM engineering_workspaces WHERE run=? AND assignment IS NOT NULL',(run,)).fetchone(): raise ManualLifecycleError('engineering workspace claim remains unresolved')
            db.execute("UPDATE manual_assignments SET state='stopped',attestation=? WHERE run=?",(attestation,run))
            db.execute("UPDATE manual_workers SET state='stopped' WHERE target=?",(self.target['manual_target'],))
            db.execute("UPDATE jobs SET status='cancelled',token=NULL,expires=NULL WHERE run=?",(run,))
            db.execute('COMMIT'); return self.status(run)
        except Exception: db.execute('ROLLBACK'); raise
    def register_replacement(self,old,new,attestation):
        if old!=self.incarnation or not isinstance(new,str) or not new.strip() or len(new)>200 or new==old or attestation!='replacement_started': raise ManualLifecycleError('exact old and distinct replacement identities required')
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            target=self.target['manual_target']
            row=db.execute('SELECT incarnation,state FROM manual_workers WHERE target=?',(target,)).fetchone()
            if row!=(old,'stopped') or db.execute('SELECT 1 FROM manual_incarnation_history WHERE target=? AND incarnation=?',(target,new)).fetchone(): raise ManualLifecycleError('replacement stale or already used')
            db.execute('INSERT INTO manual_incarnation_history VALUES(?,?)',(target,new))
            db.execute("UPDATE manual_workers SET incarnation=?,state='available' WHERE target=?",(new,target))
            db.execute('COMMIT'); self.incarnation=new
            return {'incarnation':new,'confirmation':'operator-attested'}
        except Exception: db.execute('ROLLBACK'); raise

    def register_checkpoint_replacement(self,run,old,new):
        if old!=self.incarnation or not isinstance(new,str) or not new.strip() or len(new)>200 or new==old: raise ManualLifecycleError('exact old and distinct replacement identities required')
        from runner.checkpoint import ensure_quiescent
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            target=self.target['manual_target']
            worker=db.execute('SELECT incarnation,state FROM manual_workers WHERE target=?',(target,)).fetchone()
            assignment=db.execute('SELECT target,incarnation,state FROM manual_assignments WHERE run=?',(run,)).fetchone()
            job=db.execute('SELECT status,token,expires FROM jobs WHERE run=?',(run,)).fetchone()
            graph=db.execute('SELECT status FROM graph_runs WHERE id=?',(run,)).fetchone()
            if worker!=(old,'checkpointed') or assignment!=(target,old,'checkpointed') or job!=('queued',None,None) or graph!=('active',) or db.execute('SELECT 1 FROM graph_claims WHERE run=?',(run,)).fetchone() or db.execute('SELECT 1 FROM manual_incarnation_history WHERE target=? AND incarnation=?',(target,new)).fetchone(): raise ManualLifecycleError('checkpoint replacement stale or already used')
            ensure_quiescent(db,run)
            db.execute('INSERT INTO manual_incarnation_history VALUES(?,?)',(target,new))
            if db.execute("UPDATE manual_assignments SET incarnation=? WHERE run=? AND target=? AND incarnation=? AND state='checkpointed'",(new,run,target,old)).rowcount!=1: raise ManualLifecycleError('assignment changed')
            if db.execute("UPDATE manual_workers SET incarnation=?,state='available' WHERE target=? AND incarnation=? AND state='checkpointed'",(new,target,old)).rowcount!=1: raise ManualLifecycleError('worker changed')
            if db.execute('INSERT INTO checkpoint_replacements(run,target,old_incarnation,new_incarnation,created) VALUES(?,?,?,?,?)',(run,target,old,new,time.time())).rowcount!=1: raise ManualLifecycleError('replacement history unavailable')
            if db.execute("INSERT INTO checkpoint_audit(schema_version,run,status,created) VALUES(1,?,'replacement_registered',?)",(run,time.time())).rowcount!=1: raise ManualLifecycleError('checkpoint audit unavailable')
            db.execute('COMMIT'); return {'incarnation':new}
        except Exception: db.execute('ROLLBACK'); raise
