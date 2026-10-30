"""One-use human authorization and uncertain external action tracking."""
import sqlite3
import time

class ActionConflict(RuntimeError):
    pass

class ActionLedger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.execute('''CREATE TABLE IF NOT EXISTS external_actions (
            run TEXT NOT NULL, action TEXT NOT NULL, version TEXT NOT NULL,
            actor TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL,
            PRIMARY KEY(run, action, version))''')
        self.db.execute('CREATE TABLE IF NOT EXISTS action_authorization_history(run TEXT NOT NULL, action TEXT NOT NULL, version TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS action_scopes(scope TEXT PRIMARY KEY, run TEXT NOT NULL, action TEXT NOT NULL, version TEXT NOT NULL)')
        self.db.execute("CREATE TABLE IF NOT EXISTS action_followups(run TEXT NOT NULL, action TEXT NOT NULL, version TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', PRIMARY KEY(run,action,version,kind))")
        if 'target' not in [row[1] for row in self.db.execute('PRAGMA table_info(action_followups)')]: self.db.execute("ALTER TABLE action_followups ADD COLUMN target TEXT NOT NULL DEFAULT ''")
        self.db.execute('CREATE TABLE IF NOT EXISTS followup_evidence(run TEXT NOT NULL, action TEXT NOT NULL, version TEXT NOT NULL, kind TEXT NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(run,action,version,kind))')

    def approve(self, run, action, version, actor):
        if not all((run, action, version, actor)): raise ValueError('all fields are required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM action_followups WHERE run=?',(run,)).fetchone(): raise ActionConflict('required followup pending')
            if self.db.execute("SELECT 1 FROM external_actions WHERE run=? AND action=? AND status='uncertain'", (run, action)).fetchone(): raise ActionConflict('prior execution unresolved')
            row = self.db.execute('SELECT status FROM external_actions WHERE run=? AND action=? AND version=?', (run, action, version)).fetchone()
            if row and row[0] != 'not_executed': raise ActionConflict('action already approved or execution unresolved')
            self.db.execute('INSERT OR REPLACE INTO external_actions VALUES (?,?,?,?,?,?)', (run, action, version, actor, 'approved', time.time()))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def reauthorize_completed(self,run,action,version,actor):
        if not actor: raise ValueError('actor required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT status FROM external_actions WHERE run=? AND action=? AND version=?',(run,action,version)).fetchone()
            if not row or row[0]!='executed' or self.db.execute('SELECT 1 FROM action_scopes WHERE run=?',(run,)).fetchone(): raise ActionConflict('reauthorization unavailable')
            self.db.execute('INSERT INTO action_authorization_history VALUES(?,?,?,?,?)',(run,action,version,actor,time.time()))
            self.db.execute("UPDATE external_actions SET status='approved',actor=?,updated=? WHERE run=? AND action=? AND version=?",(actor,time.time(),run,action,version))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise

    def reserve(self, run, action, version):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute("SELECT 1 FROM external_actions WHERE run=? AND action=? AND status='uncertain'",(run,action)).fetchone(): raise ActionConflict('prior execution unresolved')
            cur = self.db.execute("UPDATE external_actions SET status='uncertain', updated=? WHERE run=? AND action=? AND version=? AND status='approved'", (time.time(), run, action, version))
            if not cur.rowcount: raise ActionConflict('exact unused approval required')
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise

    def reserve_scoped(self,run,action,version,scope):
        if not scope: raise ValueError('scope required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM action_followups WHERE run=?',(run,)).fetchone(): raise ActionConflict('required followup pending')
            if self.db.execute('SELECT 1 FROM action_scopes WHERE scope IN (?,?)',(scope,'run:'+run)).fetchone(): raise ActionConflict('prior resource or task operation unresolved')
            cur=self.db.execute("UPDATE external_actions SET status='uncertain', updated=? WHERE run=? AND action=? AND version=? AND status='approved'",(time.time(),run,action,version))
            if not cur.rowcount: raise ActionConflict('exact unused approval required')
            self.db.execute('INSERT INTO action_scopes VALUES(?,?,?,?)',(scope,run,action,version))
            self.db.execute('INSERT INTO action_scopes VALUES(?,?,?,?)',('run:'+run,run,action,version))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise

    def resolve_scoped(self,run,action,version,scope,result,followup=None):
        if result not in ('executed','not_executed','desired_state_observed'): raise ValueError('confirmed result required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT run,action,version FROM action_scopes WHERE scope=?',(scope,)).fetchone()
            if row!=(run,action,version): raise ActionConflict('resource operation unavailable')
            cur=self.db.execute("UPDATE external_actions SET status=?, updated=? WHERE run=? AND action=? AND version=? AND status='uncertain'",(result,time.time(),run,action,version))
            if not cur.rowcount: raise ActionConflict('operation unavailable')
            if followup:
                if result not in ('executed','desired_state_observed'): raise ValueError('followup requires confirmed action')
                self.db.execute('INSERT OR IGNORE INTO action_followups(run,action,version,kind,target) VALUES(?,?,?,?,?)',(run,action,version,followup,scope))
            self.db.execute('DELETE FROM action_scopes WHERE scope IN (?,?)',(scope,'run:'+run))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise

    def complete_followup(self,run,version,kind,target,evidence,lock_check):
        if not target or not evidence or not lock_check: raise ActionConflict('verified evidence and claim required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if lock_check(run) is not True: raise ActionConflict('task claim unavailable')
            rows=self.db.execute('SELECT action FROM action_followups WHERE run=? AND version=? AND kind=? AND target=?',(run,version,kind,target)).fetchall()
            if len(rows)!=1: raise ActionConflict('followup unavailable or ambiguous')
            action=rows[0][0]
            self.db.execute('INSERT OR REPLACE INTO followup_evidence VALUES(?,?,?,?,?)',(run,action,version,kind,evidence))
            self.db.execute('DELETE FROM action_followups WHERE run=? AND action=? AND version=? AND kind=?',(run,action,version,kind))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise

    def pending_followups(self,run):
        return self.db.execute('SELECT action,version,kind FROM action_followups WHERE run=? ORDER BY action,version,kind',(run,)).fetchall()

    def resolve(self, run, action, version, result):
        if result not in ('executed', 'not_executed'): raise ValueError('resolution must be confirmed')
        cur = self.db.execute("UPDATE external_actions SET status=?, updated=? WHERE run=? AND action=? AND version=? AND status='uncertain'", (result, time.time(), run, action, version))
        if not cur.rowcount: raise ActionConflict('no uncertain action')
