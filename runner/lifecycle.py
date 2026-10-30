"""Durable manual/optional automatic worker-stop coordination.

Observer and controller are trusted, separately provisioned components, never
worker or model inputs. No controller credentials or live endpoints are set up.
"""
import hashlib
import json
import sqlite3
import threading
import re
import secrets
import urllib.parse

class LifecycleError(RuntimeError): pass

class Lifecycle:
    FIELDS={'origin','service','application','controller_id','permission_scope','verifier_id'}
    def __init__(self,path,target,observer,controller=None,timeout=15):
        if not isinstance(target,dict) or set(target)!=self.FIELDS or any(not isinstance(value,str) or not value.strip() for value in target.values()): raise ValueError('complete exact lifecycle policy required')
        try:
            origin=urllib.parse.urlsplit(target['origin']); host=origin.hostname; port=origin.port
            canonical=host if port is None else f'{host}:{port}'
            if origin.scheme!='https' or not host or host.endswith('.') or origin.netloc!=canonical or port==443 or origin.path not in ('','/') or origin.query or origin.fragment: raise ValueError('canonical HTTPS origin required')
        except (ValueError,TypeError): raise ValueError('canonical HTTPS origin required') from None
        if any(not re.fullmatch(r'[a-z0-9_-]{1,128}',target[key]) for key in ('service','application')): raise ValueError('canonical target IDs required')
        if timeout<=0: raise ValueError('positive timeout required')
        self.target=dict(target); self.target['origin']=self.target['origin'].rstrip('/'); self.observer=observer; self.controller=controller; self.auto=False; self.timeout=timeout; self.inflight={}
        self.policy_hash=hashlib.sha256(json.dumps(self.target,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        physical={key:self.target[key] for key in ('origin','service','application')}
        self.target_hash=hashlib.sha256(json.dumps(physical,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None)
        self.db.execute('''CREATE TABLE IF NOT EXISTS worker_lifecycle(
            run TEXT PRIMARY KEY, policy_hash TEXT NOT NULL, incarnation TEXT NOT NULL,
            status TEXT NOT NULL, manual_requested INTEGER NOT NULL DEFAULT 0, replacement TEXT, target_hash TEXT NOT NULL DEFAULT '')''')
        columns=[row[1] for row in self.db.execute('PRAGMA table_info(worker_lifecycle)')]
        if 'target_hash' not in columns: self.db.execute("ALTER TABLE worker_lifecycle ADD COLUMN target_hash TEXT NOT NULL DEFAULT ''")
        self.db.execute("UPDATE worker_lifecycle SET target_hash=? WHERE target_hash='' AND policy_hash=?",(self.target_hash,self.policy_hash))
        if self.db.execute("SELECT 1 FROM worker_lifecycle WHERE target_hash='' LIMIT 1").fetchone(): raise LifecycleError('legacy lifecycle target requires manual migration')
        self.db.execute('DROP INDEX IF EXISTS lifecycle_incarnation')
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS lifecycle_physical_incarnation ON worker_lifecycle(target_hash,incarnation)')
        self.db.execute('CREATE TABLE IF NOT EXISTS lifecycle_operations(target_hash TEXT PRIMARY KEY,run TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS lifecycle_calls(target_hash TEXT PRIMARY KEY,run TEXT NOT NULL,call_id TEXT NOT NULL,active INTEGER NOT NULL)')
        self.db_path=self.db.execute('PRAGMA database_list').fetchone()[2]
        self.db.execute('CREATE TABLE IF NOT EXISTS lifecycle_settings(policy_hash TEXT PRIMARY KEY,enabled INTEGER NOT NULL)')
    def _call(self,function,*args,tracking_key=None,on_complete=None):
        done=threading.Event(); result=[]
        if tracking_key is not None:
            existing=self.inflight.get(tracking_key)
            if existing is not None and not existing.is_set(): raise LifecycleError('lifecycle operation still in flight')
            self.inflight[tracking_key]=done
        def invoke():
            try: result.append((True,function(*args)))
            except Exception as error: result.append((False,error))
            finally:
                if on_complete:
                    try: on_complete()
                    except Exception: pass
                done.set()
        threading.Thread(target=invoke,daemon=True).start()
        if not done.wait(self.timeout): raise LifecycleError('lifecycle operation timed out')
        if not result[0][0]: raise LifecycleError('lifecycle operation failed') from None
        return result[0][1]
    def close(self): self.db.close()
    def enable_auto(self,*,approved_policy,verify_policy):
        # verify_policy is a trusted operator-approved integration test verifier.
        if not self.controller or approved_policy!=self.policy_hash or not callable(verify_policy) or self._call(verify_policy,self.policy_hash) is not True: raise LifecycleError('automatic controller not approved and verified')
        self.db.execute('INSERT OR REPLACE INTO lifecycle_settings VALUES(?,1)',(self.policy_hash,))
        self.auto=True
    def restore_auto(self,verify_policy):
        row=self.db.execute('SELECT enabled FROM lifecycle_settings WHERE policy_hash=?',(self.policy_hash,)).fetchone()
        if not row or row[0]!=1 or not self.controller or not callable(verify_policy) or self._call(verify_policy,self.policy_hash) is not True: raise LifecycleError('automatic policy not verified')
        self.auto=True
    def disable_auto(self):
        self.auto=False
        self.db.execute('INSERT OR REPLACE INTO lifecycle_settings VALUES(?,0)',(self.policy_hash,))
    def register(self,run,incarnation):
        if not run or not incarnation or self._call(self.observer.current_incarnation,self.target)!=incarnation: raise LifecycleError('worker identity not verified')
        self.db.execute('INSERT INTO worker_lifecycle(run,policy_hash,incarnation,status,target_hash) VALUES(?,?,?,?,?)',(run,self.policy_hash,incarnation,'running',self.target_hash))
    def status(self,run):
        row=self.db.execute('SELECT policy_hash,incarnation,status,manual_requested,replacement FROM worker_lifecycle WHERE run=?',(run,)).fetchone()
        if not row or row[0]!=self.policy_hash: raise LifecycleError('worker policy unavailable')
        return {'incarnation':row[1],'status':('uncertain' if row[2]=='requesting' else 'restart_uncertain' if row[2]=='restart_requesting' else row[2]),'manual_requested':bool(row[3]),'replacement':row[4]}
    def _reserve_target(self,run):
        owner=self.db.execute('SELECT run FROM lifecycle_operations WHERE target_hash=?',(self.target_hash,)).fetchone()
        if owner and owner[0]!=run: raise LifecycleError('target lifecycle operation unresolved')
        self.db.execute('INSERT OR IGNORE INTO lifecycle_operations VALUES(?,?)',(self.target_hash,run))
    def _release_target(self,run):
        cur=self.db.execute('DELETE FROM lifecycle_operations WHERE target_hash=? AND run=?',(self.target_hash,run))
        if not cur.rowcount: raise LifecycleError('target lifecycle reservation unavailable')
    def _record_call(self,run):
        call_id=secrets.token_urlsafe(24)
        if self.db.execute('SELECT 1 FROM lifecycle_calls WHERE target_hash=? AND active=1',(self.target_hash,)).fetchone(): raise LifecycleError('target call still active')
        self.db.execute('INSERT OR REPLACE INTO lifecycle_calls VALUES(?,?,?,1)',(self.target_hash,run,call_id))
        return call_id
    def _complete_call(self,run,call_id):
        # A separate connection is necessary because this callback runs on a worker thread.
        db=sqlite3.connect(self.db_path,timeout=5,isolation_level=None)
        try: db.execute('UPDATE lifecycle_calls SET active=0 WHERE target_hash=? AND run=? AND call_id=?',(self.target_hash,run,call_id))
        finally: db.close()
    def _assert_quiescent(self,run,state,evidence):
        row=self.db.execute('SELECT active FROM lifecycle_calls WHERE target_hash=? AND run=?',(self.target_hash,run)).fetchone()
        if row and row[0]: raise LifecycleError('lifecycle call still active')
        if state in ('uncertain','restart_uncertain'):
            verifier=getattr(self.observer,'verify_operation_quiesced',None)
            if not callable(verifier) or self._call(verifier,self.target,run,evidence) is not True: raise LifecycleError('remote lifecycle outcome not fenced')
    def resolve_interrupted_call(self,run,evidence):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.status(run)
            if state['status'] not in ('uncertain','restart_uncertain'): raise LifecycleError('no uncertain lifecycle call')
            if run in self.inflight and not self.inflight[run].is_set(): raise LifecycleError('lifecycle call still active')
            verifier=getattr(self.observer,'verify_operation_quiesced',None)
            if not callable(verifier) or self._call(verifier,self.target,run,evidence) is not True: raise LifecycleError('request quiescence not independently verified')
            cur=self.db.execute('UPDATE lifecycle_calls SET active=0 WHERE target_hash=? AND run=? AND active=1',(self.target_hash,run))
            if not cur.rowcount: raise LifecycleError('interrupted call unavailable')
            self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
    def manual_stop(self,run):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.status(run)
            if state['status']=='stopped': raise LifecycleError('worker already stopped')
            if state['status'].startswith('restart') or state['status']=='restarted': raise LifecycleError('register replacement as a new run before stopping it')
            current=self._call(self.observer.current_incarnation,self.target)
            if current is not None and current!=state['incarnation']: raise LifecycleError('worker incarnation changed; inspect before stopping')
            self._reserve_target(run)
            self.db.execute("UPDATE worker_lifecycle SET manual_requested=1,status=CASE WHEN status='running' THEN 'pending' ELSE status END WHERE run=?",(run,))
            self.db.execute('COMMIT')
            return {'origin':self.target['origin'],'service':self.target['service'],'application':self.target['application'],'incarnation':state['incarnation'],'observed_current':current,'evidence_required':'Independently verify this incarnation is terminated and the target is fenced against restart; if no current incarnation is observed, do not issue a stop against an unknown replacement.'}
        except Exception: self.db.execute('ROLLBACK'); raise
    def automatic_stop(self,run):
        if not self.auto or not self.controller: raise LifecycleError('automatic controller unavailable')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.status(run)
            if state['status']=='requested': self.db.execute('COMMIT'); return
            if state['status'] not in ('running','pending'): raise LifecycleError('stop outcome unresolved or worker stopped')
            if self._call(self.observer.current_incarnation,self.target)!=state['incarnation']: raise LifecycleError('worker incarnation changed')
            self._reserve_target(run)
            call_id=self._record_call(run)
            self.db.execute("UPDATE worker_lifecycle SET status='requesting' WHERE run=?",(run,)); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
        def issue(target):
            self.controller.stop(target)
            return True
        try:
            if self._call(self.observer.run_if_current,self.target,state['incarnation'],issue,tracking_key=run,on_complete=lambda:self._complete_call(run,call_id)) is not True: raise LifecycleError('guard did not issue stop')
        except Exception:
            self.db.execute("UPDATE worker_lifecycle SET status='uncertain' WHERE run=? AND status='requesting'",(run,))
            raise LifecycleError('stop outcome uncertain; inspect before retry') from None
        self.db.execute("UPDATE worker_lifecycle SET status='requested' WHERE run=? AND status='requesting'",(run,))
    def confirm_stopped(self,run,evidence):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.status(run)
            if state['status'] not in ('pending','requested','uncertain','stopped'): raise LifecycleError('stop not requested')
            if run in self.inflight and not self.inflight[run].is_set(): raise LifecycleError('lifecycle operation still in flight')
            self._assert_quiescent(run,state['status'],evidence)
            if self._call(self.observer.verify_stopped_and_fenced,self.target,state['incarnation'],evidence) is not True: raise LifecycleError('termination not independently verified')
            if state['status']=='stopped': self.db.execute('COMMIT'); return
            self._release_target(run)
            self.db.execute("UPDATE worker_lifecycle SET status='stopped' WHERE run=?",(run,)); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise

    def manual_restart(self,run):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.status(run)
            if state['status'] not in ('stopped','restart_pending','restart_requested','restart_uncertain'): raise LifecycleError('stop must be verified first')
            self._reserve_target(run)
            if state['status']=='stopped': self.db.execute("UPDATE worker_lifecycle SET status='restart_pending' WHERE run=?",(run,))
            self.db.execute('COMMIT')
            return {'origin':self.target['origin'],'service':self.target['service'],'application':self.target['application'],'incarnation':state['incarnation'],'evidence_required':'Verify the old incarnation remains terminated and the replacement has a distinct identity; do not replay uncertain jobs.'}
        except Exception: self.db.execute('ROLLBACK'); raise
    def automatic_restart(self,run):
        if not self.auto or not self.controller: raise LifecycleError('automatic controller unavailable')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.status(run)
            if state['status']=='restart_requested': self.db.execute('COMMIT'); return
            if state['status'] not in ('stopped','restart_pending'): raise LifecycleError('restart outcome unresolved or worker not stopped')
            if self._call(self.observer.current_incarnation,self.target) is not None: raise LifecycleError('target already running')
            self._reserve_target(run)
            call_id=self._record_call(run)
            self.db.execute("UPDATE worker_lifecycle SET status='restart_requesting' WHERE run=?",(run,)); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
        def issue(target):
            self.controller.restart(target)
            return True
        try:
            if self._call(self.observer.run_if_stopped,self.target,state['incarnation'],issue,tracking_key=run,on_complete=lambda:self._complete_call(run,call_id)) is not True: raise LifecycleError('guard did not issue restart')
        except Exception:
            self.db.execute("UPDATE worker_lifecycle SET status='restart_uncertain' WHERE run=? AND status='restart_requesting'",(run,))
            raise LifecycleError('restart outcome uncertain; inspect before retry') from None
        self.db.execute("UPDATE worker_lifecycle SET status='restart_requested' WHERE run=? AND status='restart_requesting'",(run,))
    def confirm_restarted(self,run,evidence):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.status(run)
            if state['status'] not in ('restart_pending','restart_requested','restart_uncertain'): raise LifecycleError('restart not requested')
            if run in self.inflight and not self.inflight[run].is_set(): raise LifecycleError('lifecycle operation still in flight')
            self._assert_quiescent(run,state['status'],evidence)
            new=self._call(self.observer.verify_restarted,self.target,state['incarnation'],evidence)
            if not isinstance(new,str) or not new or new==state['incarnation'] or self._call(self.observer.current_incarnation,self.target)!=new: raise LifecycleError('replacement not independently verified')
            self._release_target(run)
            self.db.execute("UPDATE worker_lifecycle SET status='restarted',replacement=? WHERE run=?",(new,run)); self.db.execute('COMMIT')
            return new
        except Exception: self.db.execute('ROLLBACK'); raise
