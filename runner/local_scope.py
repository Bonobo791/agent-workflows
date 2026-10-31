"""Durable identities and termination evidence for local systemd scopes.

A reserved scope is not proof of launch. Ambiguous starts and stops stay fenced.
"""
from pathlib import Path, PurePosixPath
import re
import secrets
import sqlite3
import subprocess

class ScopeError(RuntimeError): pass

def valid_scope_identity(unit,invocation,cgroup):
    return (isinstance(unit,str) and bool(unit) and isinstance(invocation,str) and re.fullmatch(r'[0-9a-f]{32}',invocation) is not None
            and isinstance(cgroup,str) and cgroup.startswith('/') and cgroup.endswith('/'+unit) and '..' not in PurePosixPath(cgroup).parts)

def valid_terminated_scope(db,run,unit):
    row=db.execute("SELECT invocation,cgroup FROM local_scopes WHERE run=? AND unit=? AND state='terminated'",(run,unit)).fetchone()
    return bool(row and valid_scope_identity(unit,*row))

def unresolved_scope_rows(db,run):
    rows=db.execute('SELECT unit,invocation,cgroup,state FROM local_scopes WHERE run=? ORDER BY unit',(run,)).fetchall()
    return [(unit,'unverified' if state=='terminated' else state) for unit,invocation,cgroup,state in rows
            if state!='terminated' or not valid_scope_identity(unit,invocation,cgroup)]

class SystemdScopes:
    def __init__(self, environment):
        self.environment=dict(environment)
    def _run(self,*args):
        try:
            return subprocess.run(['/usr/bin/systemctl','--user',*args],env=self.environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=15,check=True).stdout
        except (OSError,subprocess.SubprocessError): raise ScopeError('scope operation unavailable') from None
    def inspect(self,unit):
        result=self._run('show',unit,'--property=Id','--property=LoadState','--property=ActiveState','--property=InvocationID','--property=ControlGroup')
        fields={}
        for line in result.splitlines():
            key,sep,value=line.partition('=')
            if not sep or key in fields: raise ScopeError('invalid scope inspection')
            fields[key]=value
        if set(fields)!={'Id','LoadState','ActiveState','InvocationID','ControlGroup'}: raise ScopeError('invalid scope inspection')
        return dict(id=fields['Id'],load=fields['LoadState'],active=fields['ActiveState'],invocation=fields['InvocationID'],cgroup=fields['ControlGroup'])
    def stop(self,unit): self._run('stop',unit); return True
    def empty_cgroup(self,path):
        parts=PurePosixPath(path).parts
        if not path.startswith('/') or not parts or any(p in ('.','..') for p in parts): return False
        if not Path('/sys/fs/cgroup/cgroup.controllers').is_file(): return False
        target=Path('/sys/fs/cgroup').joinpath(*parts[1:])/'cgroup.events'
        try:
            values=dict(line.split() for line in target.read_text().splitlines())
            return values.get('populated')=='0'
        except FileNotFoundError: return True
        except (OSError,ValueError): return False

class LocalScopes:
    def __init__(self,path,backend):
        self.backend=backend
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None)
        if not self.db.execute('PRAGMA database_list').fetchone()[2]: raise ScopeError('durable database required')
        self.db.execute('CREATE TABLE IF NOT EXISTS local_scopes(run TEXT NOT NULL,unit TEXT PRIMARY KEY,invocation TEXT,cgroup TEXT,state TEXT NOT NULL)')
        try: self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS local_scopes_live_v2 ON local_scopes(run) WHERE state IS NOT 'terminated'")
        except sqlite3.IntegrityError:
            self.db.close(); raise ScopeError('conflicting unresolved scopes') from None
        self.db.execute('CREATE TABLE IF NOT EXISTS local_scope_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,unit TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL)')
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS local_scope_audit_insert AFTER INSERT ON local_scopes BEGIN
            INSERT INTO local_scope_audit(schema_version,run,unit,state,created) VALUES(1,NEW.run,NEW.unit,NEW.state,(julianday('now')-2440587.5)*86400.0); END""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS local_scope_audit_update AFTER UPDATE OF state ON local_scopes WHEN OLD.state != NEW.state BEGIN
            INSERT INTO local_scope_audit(schema_version,run,unit,state,created) VALUES(1,NEW.run,NEW.unit,NEW.state,(julianday('now')-2440587.5)*86400.0); END""")
    def close(self): self.db.close()
    def reserve(self,run):
        if not isinstance(run,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',run): raise ScopeError('invalid run')
        unit='agent-workflows-'+secrets.token_hex(24)+'.scope'
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if unresolved_scope_rows(self.db,run): raise ScopeError('prior scope unresolved')
            self.db.execute("INSERT INTO local_scopes(run,unit,state) VALUES(?,?,'starting')",(run,unit))
            self.db.execute('COMMIT')
        except sqlite3.IntegrityError:
            self.db.execute('ROLLBACK'); raise ScopeError('prior scope unresolved') from None
        except Exception:
            self.db.execute('ROLLBACK'); raise
        return unit
    def state(self,run,unit):
        row=self.db.execute('SELECT state FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone()
        if not row: raise ScopeError('scope assignment unavailable')
        return row[0]
    def inspect(self,run,unit):
        row=self.db.execute('SELECT invocation,cgroup,state FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone()
        if not row: raise ScopeError('scope assignment unavailable')
        try: observed=self.backend.inspect(unit)
        except ScopeError: observed=None
        return {'recorded':{'unit':unit,'state':row[2],'invocation':row[0],'cgroup':row[1]},
                'observed':observed,'cgroup_empty':self.backend.empty_cgroup(row[1]) if row[1] and observed is not None else None}
    def bind(self,run,unit):
        info=self.backend.inspect(unit)
        group=info.get('cgroup','')
        if info.get('id')!=unit or info.get('load')!='loaded' or info.get('active')!='active' or not valid_scope_identity(unit,info.get('invocation'),group): raise ScopeError('active scope identity unverified')
        changed=self.db.execute("UPDATE local_scopes SET state='running',invocation=?,cgroup=? WHERE run=? AND unit=? AND state='starting'",(info['invocation'],group,run,unit)).rowcount
        if changed!=1: raise ScopeError('scope assignment changed')
    def assert_active(self,run,unit):
        row=self.db.execute('SELECT invocation,cgroup,state FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone()
        if not row or row[2]!='running': raise ScopeError('running scope unavailable')
        info=self.backend.inspect(unit)
        if info.get('id')!=unit or info.get('load')!='loaded' or info.get('active')!='active' or info.get('invocation')!=row[0] or info.get('cgroup')!=row[1]: raise ScopeError('scope identity changed')
        return True
    def stop(self,run,unit):
        self.assert_active(run,unit)
        row=self.db.execute('SELECT invocation,cgroup,state FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone()
        changed=self.db.execute("UPDATE local_scopes SET state='stopping' WHERE run=? AND unit=? AND state='running'",(run,unit)).rowcount
        if changed!=1: raise ScopeError('scope assignment changed')
        try:
            if self.backend.stop(unit) is not True: raise ScopeError('scope stop uncertain')
            after=self.backend.inspect(unit)
            if after.get('id')!=unit or after.get('active')!='inactive' or after.get('load') not in ('loaded','not-found') or (after.get('load')=='loaded' and after.get('invocation') not in ('',row[0])) or not self.backend.empty_cgroup(row[1]): raise ScopeError('scope termination unverified')
            if self.db.execute("UPDATE local_scopes SET state='terminated' WHERE run=? AND unit=? AND state='stopping'",(run,unit)).rowcount!=1: raise ScopeError('scope assignment changed')
            return True
        except Exception: raise ScopeError('scope stop uncertain; run remains fenced') from None
    def reconcile_stop(self,run,unit,verify_quiescent):
        row=self.db.execute('SELECT invocation,cgroup,state FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone()
        if not row or row[2]!='stopping' or not callable(verify_quiescent): raise ScopeError('uncertain scope stop unavailable')
        try:
            if verify_quiescent(run,unit,row[0],row[1]) is not True: raise ScopeError('stop request may still be in flight')
            info=self.backend.inspect(unit)
            if info.get('id')!=unit or info.get('active')!='inactive' or info.get('load') not in ('loaded','not-found') or (info.get('load')=='loaded' and info.get('invocation') not in ('',row[0])) or not self.backend.empty_cgroup(row[1]): raise ScopeError('scope termination unverified')
            if verify_quiescent(run,unit,row[0],row[1]) is not True: raise ScopeError('stop request may still be in flight')
            if self.db.execute("UPDATE local_scopes SET state='terminated' WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND state='stopping'",(run,unit,row[0],row[1])).rowcount!=1: raise ScopeError('scope assignment changed')
            return True
        except Exception: raise ScopeError('scope stop remains unresolved') from None
