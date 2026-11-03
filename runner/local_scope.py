"""Durable record of local systemd scopes: one row per scope, (run, unit, state).

A scope is recorded before launch, marked running once systemd reports it active, and
terminated only after systemd reports it inactive or failed. Unresolved rows block
shutdown and manual stop.
"""
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess

class ScopeError(RuntimeError): pass

# systemd reports a scope stopped by a timeout or kill as 'failed'.
STOPPED_STATES=('inactive','failed')

def unresolved_scope_rows(db,run):
    return db.execute("SELECT unit,state FROM local_scopes WHERE run=? AND state IS NOT 'terminated' ORDER BY unit",(run,)).fetchall()

def reserve_scope(db,run):
    if not db.in_transaction: raise ScopeError('scope reservation transaction required')
    if not isinstance(run,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',run): raise ScopeError('invalid run')
    if unresolved_scope_rows(db,run): raise ScopeError('prior scope unresolved')
    unit='agent-workflows-'+secrets.token_hex(24)+'.scope'
    db.execute("INSERT INTO local_scopes(run,unit,state) VALUES(?,?,'starting')",(run,unit))
    return unit

def require_user_manager(backend):
    check=getattr(backend,'require_user_manager',None)
    if callable(check): check()

def reserve_for_execution(db,run,backend,workspace,workspace_claim=None):
    require_user_manager(backend)
    scopes=LocalScopes(db,backend)
    try:
        if workspace_claim is None: return scopes.reserve(run)
        from runner.engineering_workspace import EngineeringWorkspace
        if not isinstance(workspace_claim,tuple) or len(workspace_claim)!=3 or Path(db).resolve()!=Path(workspace_claim[0]).resolve()/'state.db': raise ScopeError('workspace claim state mismatch')
        registry=EngineeringWorkspace(workspace_claim[0])
        try:
            _,unit=registry.claim_local(run,workspace_claim[1],workspace_claim[2],expected_path=str(workspace))
            return unit
        finally: registry.close()
    finally: scopes.close()

class SystemdScopes:
    def __init__(self, environment):
        self.environment=dict(environment)
    def _run(self,*args):
        try:
            return subprocess.run(['/usr/bin/systemctl','--user',*args],env=self.environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=15,check=True).stdout
        except subprocess.CalledProcessError as exc:
            raise ScopeError(f'systemctl --user {args[0]} failed: {(exc.stderr or "").strip()[:300]}') from exc
        except (OSError,subprocess.SubprocessError) as exc: raise ScopeError('scope operation unavailable') from exc
    def require_user_manager(self):
        try: self._run('show-environment')
        except ScopeError as exc: raise ScopeError(f'systemctl --user cannot reach a user manager; start a user session manager ({exc})') from exc
    def inspect(self,unit):
        result=self._run('show',unit,'--property=Id','--property=LoadState','--property=ActiveState')
        fields={}
        for line in result.splitlines():
            key,sep,value=line.partition('=')
            if not sep or key in fields: raise ScopeError('invalid scope inspection')
            fields[key]=value
        if set(fields)!={'Id','LoadState','ActiveState'}: raise ScopeError('invalid scope inspection')
        return dict(id=fields['Id'],load=fields['LoadState'],active=fields['ActiveState'])
    def stop(self,unit): self._run('stop',unit); return True

class LocalScopes:
    def __init__(self,path,backend):
        self.backend=backend
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None)
        if not self.db.execute('PRAGMA database_list').fetchone()[2]: raise ScopeError('durable database required')
        self.db.execute('CREATE TABLE IF NOT EXISTS local_scopes(run TEXT NOT NULL,unit TEXT PRIMARY KEY,state TEXT NOT NULL)')
        # Enforces one unresolved scope per run.
        try: self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS local_scopes_live_v2 ON local_scopes(run) WHERE state IS NOT 'terminated'")
        except sqlite3.IntegrityError as exc:
            self.db.close(); raise ScopeError('conflicting unresolved scopes') from exc
    def close(self): self.db.close()
    def reserve(self,run):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            unit=reserve_scope(self.db,run)
            self.db.execute('COMMIT'); return unit
        except sqlite3.IntegrityError as exc:
            self.db.execute('ROLLBACK'); raise ScopeError('scope reservation unavailable') from exc
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def state(self,run,unit):
        row=self.db.execute('SELECT state FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone()
        if not row: raise ScopeError('scope assignment unavailable')
        return row[0]
    def inspect(self,run,unit):
        row=self.db.execute('SELECT state FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone()
        if not row: raise ScopeError('scope assignment unavailable')
        try: observed=self.backend.inspect(unit)
        except ScopeError: observed=None
        return {'recorded':{'unit':unit,'state':row[0]},'observed':observed}
    def bind(self,run,unit):
        info=self.backend.inspect(unit)
        if info.get('id')!=unit or info.get('load')!='loaded' or info.get('active')!='active': raise ScopeError('active scope unverified')
        if self.db.execute("UPDATE local_scopes SET state='running' WHERE run=? AND unit=? AND state='starting'",(run,unit)).rowcount!=1: raise ScopeError('scope assignment changed')
    def assert_active(self,run,unit):
        if not self.db.execute("SELECT 1 FROM local_scopes WHERE run=? AND unit=? AND state='running'",(run,unit)).fetchone(): raise ScopeError('running scope unavailable')
        if self.backend.inspect(unit).get('active')!='active': raise ScopeError('running scope unavailable')
        return True
    def _terminate(self,run,unit):
        if self.db.execute("UPDATE local_scopes SET state='terminated' WHERE run=? AND unit=?",(run,unit)).rowcount!=1: raise ScopeError('scope assignment changed')
    def stop(self,run,unit):
        if not self.db.execute('SELECT 1 FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone(): raise ScopeError('scope assignment unavailable')
        try:
            info=self.backend.inspect(unit)
            # A unit systemd never created needs no stop request.
            if info.get('active') not in STOPPED_STATES:
                if self.backend.stop(unit) is not True: raise ScopeError('scope stop uncertain')
                info=self.backend.inspect(unit)
            if info.get('active') not in STOPPED_STATES: raise ScopeError('scope termination unverified')
            self._terminate(run,unit)
            return True
        except Exception as exc: raise ScopeError('scope stop uncertain; run remains fenced') from exc
    def reconcile(self,run,unit):
        try:
            if self.backend.inspect(unit).get('active') not in STOPPED_STATES: raise ScopeError('scope termination unverified')
            self._terminate(run,unit)
            return True
        except Exception as exc: raise ScopeError('scope stop remains unresolved') from exc
