"""Trusted executor session and skill provenance; not an agent-facing API."""
import json
import sqlite3

class ProvenanceError(RuntimeError): pass

class Provenance:
    def __init__(self,graph):
        self.graph=graph
        graph.db.execute('''CREATE TABLE IF NOT EXISTS execution_provenance (
            run TEXT NOT NULL, role TEXT NOT NULL, session TEXT NOT NULL, skills TEXT NOT NULL,
            PRIMARY KEY(run,role,session), UNIQUE(session))''')
        graph.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS provenance_global_session ON execution_provenance(session)')
        graph.db.execute("CREATE TABLE IF NOT EXISTS execution_contexts(run TEXT NOT NULL, role TEXT NOT NULL, session TEXT NOT NULL, subject TEXT NOT NULL, PRIMARY KEY(run,role,session))")
        graph.db.execute("CREATE TABLE IF NOT EXISTS execution_completions(run TEXT NOT NULL, role TEXT NOT NULL, session TEXT NOT NULL, output_hash TEXT NOT NULL DEFAULT '', PRIMARY KEY(run,role,session))")
        if 'output_hash' not in [row[1] for row in graph.db.execute('PRAGMA table_info(execution_completions)')]: graph.db.execute("ALTER TABLE execution_completions ADD COLUMN output_hash TEXT NOT NULL DEFAULT ''")
        graph.db.execute('''CREATE TABLE IF NOT EXISTS execution_audit(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, run TEXT NOT NULL, role TEXT NOT NULL, session TEXT NOT NULL, status TEXT NOT NULL, skills TEXT, output_hash TEXT, created REAL NOT NULL)''')
        graph.db.execute('''CREATE TRIGGER IF NOT EXISTS execution_audit_bind AFTER INSERT ON execution_provenance BEGIN
            INSERT INTO execution_audit(schema_version,run,role,session,status,skills,created) VALUES(1,NEW.run,NEW.role,NEW.session,'bound',NEW.skills,(julianday('now')-2440587.5)*86400.0);
        END''')
        graph.db.execute('''CREATE TRIGGER IF NOT EXISTS execution_audit_complete AFTER INSERT ON execution_completions BEGIN
            INSERT INTO execution_audit(schema_version,run,role,session,status,output_hash,created) VALUES(1,NEW.run,NEW.role,NEW.session,'completed',NEW.output_hash,(julianday('now')-2440587.5)*86400.0);
        END''')
    @staticmethod
    def _skills(skills):
        if not isinstance(skills,dict) or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v for k,v in skills.items()): raise ProvenanceError('invalid skill fingerprints')
        return json.dumps(skills,sort_keys=True,separators=(',',':'))
    def bind(self,run,role,session,skills,token,fresh=False,subject=None):
        if not isinstance(role,str) or not role or not isinstance(session,str) or not session: raise ProvenanceError('invalid session')
        encoded=self._skills(skills); db=self.graph.db
        db.execute('BEGIN IMMEDIATE')
        try:
            self.graph._lease(run,token)
            old=db.execute('SELECT run,role,skills FROM execution_provenance WHERE session=?',(session,)).fetchone()
            if old and (fresh or old != (run,role,encoded)): raise ProvenanceError('session already belongs to another role or skill set')
            db.execute('INSERT OR IGNORE INTO execution_provenance VALUES(?,?,?,?)',(run,role,session,encoded))
            if subject is not None:
                previous=db.execute('SELECT subject FROM execution_contexts WHERE run=? AND role=? AND session=?',(run,role,session)).fetchone()
                if previous and previous[0]!=subject: raise ProvenanceError('session context mismatch')
                db.execute('INSERT OR IGNORE INTO execution_contexts VALUES(?,?,?,?)',(run,role,session,subject))
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK'); raise
    def subject_matches(self,run,role,session,subject):
        row=self.graph.db.execute('SELECT subject FROM execution_contexts WHERE run=? AND role=? AND session=?',(run,role,session)).fetchone()
        return bool(row and row[0]==subject)
    def mark_completed(self,run,role,session,token,output_hash=''):
        db=self.graph.db; db.execute('BEGIN IMMEDIATE')
        try:
            self.graph._lease(run,token)
            if not db.execute('SELECT 1 FROM execution_provenance WHERE run=? AND role=? AND session=?',(run,role,session)).fetchone(): raise ProvenanceError('session unavailable')
            db.execute('INSERT OR REPLACE INTO execution_completions(run,role,session,output_hash) VALUES(?,?,?,?)',(run,role,session,output_hash))
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK'); raise
    def completion_hash(self,run,role,session):
        row=self.graph.db.execute('SELECT output_hash FROM execution_completions WHERE run=? AND role=? AND session=?',(run,role,session)).fetchone()
        return row[0] if row else None
    def completed(self,run,role,session):
        return bool(self.graph.db.execute('SELECT 1 FROM execution_completions WHERE run=? AND role=? AND session=?',(run,role,session)).fetchone())
    def verify(self,run,role,session,skills):
        row=self.graph.db.execute('SELECT skills FROM execution_provenance WHERE run=? AND role=? AND session=?',(run,role,session)).fetchone()
        return bool(row and row[0]==self._skills(skills))

def validate_review(provenance,run,role,evidence,expected_skills):
    if not isinstance(evidence,dict) or not isinstance(evidence.get('session_id'),str): return False
    return provenance.verify(run,role,evidence['session_id'],expected_skills)
