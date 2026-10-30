"""Durable local state and fail-closed engineering gates."""
import sqlite3
import time

class Conflict(Exception):
    pass

STAGES = ('plan', 'implement', 'test', 'qa', 'open_draft', 'triage', 'ready', 'post_ready')
APPROVALS = {'open_draft', 'ready'}

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, task TEXT NOT NULL UNIQUE, workflow TEXT NOT NULL, status TEXT NOT NULL, owner TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS stages(run TEXT NOT NULL REFERENCES runs(id), name TEXT NOT NULL, version TEXT NOT NULL, PRIMARY KEY(run,name));
        CREATE TABLE IF NOT EXISTS approvals(run TEXT NOT NULL REFERENCES runs(id), action TEXT NOT NULL, version TEXT NOT NULL, actor TEXT NOT NULL, PRIMARY KEY(run,action,version));
        CREATE TABLE IF NOT EXISTS sessions(run TEXT NOT NULL REFERENCES runs(id), role TEXT NOT NULL, session TEXT NOT NULL, PRIMARY KEY(run,role));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, run TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
        ''')
    def create_run(self, run, task, workflow):
        self.db.execute('INSERT INTO runs(id,task,workflow,status) VALUES(?,?,?,?)',(run,task,workflow,'pending'))
    def claim(self, run, owner, ttl, now=None):
        now = time.time() if now is None else now
        if ttl <= 0: raise ValueError('ttl must be positive')
        with self.db:
            cur = self.db.execute("UPDATE runs SET owner=?,expires=? WHERE id=? AND status NOT IN ('cancelled','completed') AND (owner IS NULL OR expires<=? OR owner=?)",(owner,now+ttl,run,now,owner))
            if not cur.rowcount: raise Conflict('run is claimed or unavailable')
    def approve(self, run, action, version, actor):
        if action not in APPROVALS or not version or not actor: raise ValueError('invalid approval')
        self.db.execute('INSERT OR REPLACE INTO approvals VALUES(?,?,?,?)',(run,action,version,actor))
    def advance(self, run, stage, version):
        if stage not in STAGES or not version: raise ValueError('invalid stage or version')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT status,workflow FROM runs WHERE id=?',(run,)).fetchone()
            if not row or row[0] in ('cancelled','completed') or row[1] != 'engineering': raise Conflict('run unavailable')
            index=STAGES.index(stage)
            previous=dict(self.db.execute('SELECT name,version FROM stages WHERE run=?',(run,)))
            if index and STAGES[index-1] not in previous: raise Conflict('previous stage missing')
            if stage in ('test','qa','open_draft','triage','ready','post_ready') and previous.get('implement') != version: raise Conflict('commit differs from implementation')
            if stage in APPROVALS and not self.db.execute('SELECT 1 FROM approvals WHERE run=? AND action=? AND version=?',(run,stage,version)).fetchone(): raise Conflict('approval required for exact version')
            if previous.get(stage) != version:
                for later in STAGES[index+1:]: self.db.execute('DELETE FROM stages WHERE run=? AND name=?',(run,later))
            self.db.execute('INSERT OR REPLACE INTO stages VALUES(?,?,?)',(run,stage,version))
            self.db.execute('UPDATE runs SET status=? WHERE id=?',('completed' if stage=='post_ready' else 'running',run))
            self.db.execute('INSERT INTO events(run,kind,detail,created) VALUES(?,?,?,?)',(run,'stage_passed',stage,time.time()))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
    def session(self, run, role, session):
        self.db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)',(run,role,session))
    def get_session(self, run, role):
        row=self.db.execute('SELECT session FROM sessions WHERE run=? AND role=?',(run,role)).fetchone()
        return row[0] if row else None
    def cancel(self, run):
        self.db.execute("UPDATE runs SET status='cancelled', owner=NULL, expires=NULL WHERE id=?",(run,))
