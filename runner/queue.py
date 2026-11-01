"""One-at-a-time durable job queue with conservative crash recovery."""
import secrets
import sqlite3
import time

class QueueError(RuntimeError): pass

class JobQueue:
    def __init__(self,path,clock=time.time):
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None); self.clock=clock
        self.db.execute('''CREATE TABLE IF NOT EXISTS jobs(
            run TEXT PRIMARY KEY, status TEXT NOT NULL, worker TEXT, token TEXT, expires REAL,
            seq INTEGER NOT NULL)''')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS job_audit(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, run TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
        CREATE TRIGGER IF NOT EXISTS job_audit_insert AFTER INSERT ON jobs BEGIN
            INSERT INTO job_audit(schema_version,run,status,created) VALUES(1,NEW.run,NEW.status,(julianday('now')-2440587.5)*86400.0);
        END;
        CREATE TRIGGER IF NOT EXISTS job_audit_update AFTER UPDATE OF status ON jobs WHEN NEW.status != OLD.status BEGIN
            INSERT INTO job_audit(schema_version,run,status,created) VALUES(1,NEW.run,NEW.status,(julianday('now')-2440587.5)*86400.0);
        END;
        ''')
    def close(self): self.db.close()
    def enqueue(self,run):
        if not isinstance(run,str) or not run: raise ValueError('run required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            seq=self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM jobs').fetchone()[0]
            self.db.execute('INSERT INTO jobs(run,status,seq) VALUES(?,?,?)',(run,'queued',seq)); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
    def claim(self,worker,ttl):
        if not worker or ttl<=0: raise ValueError('worker and positive lease required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE jobs SET status='uncertain' WHERE status IN ('running','cancelling') AND expires<=?",(self.clock(),))
            if self.db.execute("SELECT 1 FROM jobs WHERE status IN ('running','cancelling','uncertain') LIMIT 1").fetchone():
                self.db.execute('COMMIT'); return None
            row=self.db.execute("SELECT run FROM jobs WHERE status='queued' ORDER BY seq LIMIT 1").fetchone()
            if not row: self.db.execute('COMMIT'); return None
            token=secrets.token_urlsafe(32)
            self.db.execute("UPDATE jobs SET status='running',worker=?,token=?,expires=? WHERE run=?",(worker,token,self.clock()+ttl,row[0]))
            self.db.execute('COMMIT'); return {'run':row[0],'token':token}
        except Exception: self.db.execute('ROLLBACK'); raise
    def get(self,run):
        self.db.execute("UPDATE jobs SET status='uncertain' WHERE run=? AND status IN ('running','cancelling') AND expires<=?",(run,self.clock()))
        row=self.db.execute('SELECT status,worker,expires FROM jobs WHERE run=?',(run,)).fetchone()
        return dict(zip(('status','worker','expires'),row)) if row else None
    def _owned(self,run,token,states):
        row=self.db.execute('SELECT status,token,expires FROM jobs WHERE run=?',(run,)).fetchone()
        if not row or row[0] not in states or not isinstance(token,str) or not row[1] or not secrets.compare_digest(row[1],token) or row[2]<=self.clock(): raise QueueError('claim unavailable or expired')
    def may_start(self,run,token):
        try:
            self._owned(run,token,('running',))
            return True
        except QueueError: return False
    def heartbeat(self,run,token,ttl):
        if ttl<=0: raise ValueError('positive lease required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._owned(run,token,('running','cancelling'))
            self.db.execute('UPDATE jobs SET expires=? WHERE run=?',(self.clock()+ttl,run)); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
    def cancel(self,run):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT status FROM jobs WHERE run=?',(run,)).fetchone()
            if not row or row[0] not in ('queued','running'): raise QueueError('job unavailable')
            self.db.execute('UPDATE jobs SET status=? WHERE run=?',('cancelled' if row[0]=='queued' else 'cancelling',run)); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
    def finish(self,run,token,verify_completed=None,verify_final=None):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._owned(run,token,('running',))
            if not callable(verify_completed) or verify_completed(run) is not True: raise QueueError('verified workflow completion required')
            self._owned(run,token,('running',))
            last=self.db.execute('SELECT COALESCE(MAX(id),0) FROM job_audit').fetchone()[0]
            if self.db.execute("UPDATE jobs SET status='completed',token=NULL,expires=NULL WHERE run=? AND status='running'",(run,)).rowcount!=1: raise QueueError('job state changed')
            if self.db.execute('SELECT status,token,expires FROM jobs WHERE run=?',(run,)).fetchone()!=('completed',None,None) or not self.db.execute("SELECT 1 FROM job_audit WHERE id>? AND schema_version=1 AND run=? AND status='completed'",(last,run)).fetchone(): raise QueueError('completion audit unavailable')
            if verify_final is not None and (not callable(verify_final) or verify_final(run) is not True): raise QueueError('completion state changed')
            self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
    def mark_uncertain(self,run,token):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._owned(run,token,('running','cancelling'))
            self.db.execute("UPDATE jobs SET status='uncertain' WHERE run=?",(run,)); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
    def confirm_stopped(self,run,token,verify_stopped):
        # Called only by trusted infrastructure, never by a specialist or API payload.
        if not callable(verify_stopped) or verify_stopped(run) is not True: raise QueueError('worker termination not verified')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._owned(run,token,('cancelling',))
            self.db.execute("UPDATE jobs SET status='cancelled',token=NULL,expires=NULL WHERE run=?",(run,)); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise

    def resolve_uncertain(self,run,disposition,verify_stopped):
        # A verified stop is not evidence that retrying side effects is safe.
        if disposition not in ('failed','cancelled') or not callable(verify_stopped) or verify_stopped(run) is not True: raise QueueError('verified stop and terminal disposition required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            cur=self.db.execute("UPDATE jobs SET status=?,token=NULL,expires=NULL WHERE run=? AND status='uncertain'",(disposition,run))
            if not cur.rowcount: raise QueueError('uncertain job unavailable')
            self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
