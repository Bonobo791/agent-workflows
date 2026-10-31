"""Executor adapter for scoped managed-auth local processes.

The caller supplies trusted workspace, fencing, and host-policy authorities.
"""
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from runner.adapter import managed_app_server_spec
from runner.local_process import start_local_process
from runner.local_scope import LocalScopes
from runner.local_preflight import sandbox_preflight, scope_preflight
from runner.session_store import _WRITERS

class LocalSupervisorError(RuntimeError): pass

class LocalSupervisor:
    def __init__(self,db,executable,environment,backend,fence,workspace_for,verify_policy,disabled_integrations,*,engineering_root=None,policy_hash=None):
        self.db,self.executable,self.environment,self.backend=db,executable,dict(environment),backend
        self.fence,self.workspace_for,self.verify_policy=fence,workspace_for,verify_policy
        self.disabled_integrations=disabled_integrations
        self.engineering_root,self.policy_hash=engineering_root,policy_hash
        scopes=LocalScopes(db,backend); scopes.close()
        dbh=self._db()
        try:
            dbh.execute('CREATE TABLE IF NOT EXISTS local_sessions(run TEXT NOT NULL,role TEXT NOT NULL,session TEXT NOT NULL UNIQUE,binding TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(run,role))')
            dbh.execute('CREATE TABLE IF NOT EXISTS local_turn_scopes(run TEXT NOT NULL,role TEXT NOT NULL,session TEXT NOT NULL,turn TEXT NOT NULL,unit TEXT NOT NULL UNIQUE,binding TEXT NOT NULL,PRIMARY KEY(run,role,session,turn))')
            dbh.execute('CREATE TABLE IF NOT EXISTS local_turn_scope_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,role TEXT NOT NULL,session TEXT NOT NULL,turn TEXT NOT NULL,unit TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
            dbh.execute("""CREATE TRIGGER IF NOT EXISTS local_turn_scope_audit_insert AFTER INSERT ON local_turn_scopes BEGIN
                INSERT INTO local_turn_scope_audit(schema_version,run,role,session,turn,unit,status,created) VALUES(1,NEW.run,NEW.role,NEW.session,NEW.turn,NEW.unit,'bound',(julianday('now')-2440587.5)*86400.0); END""")
        finally: dbh.close()
    def _db(self): return sqlite3.connect(self.db,timeout=10,isolation_level=None)
    def preflight(self):
        try: return self.verify_policy() is True and self.fence(None) is True and sandbox_preflight(self.executable,self.environment) is True and scope_preflight(self.environment) is True
        except Exception: return False
    def _binding(self,run,role,skills,model):
        if not isinstance(skills,dict) or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v for k,v in skills.items()) or not isinstance(model,str) or not model: raise LocalSupervisorError('invalid skill or model binding')
        workspace=self.workspace_for(run,role)
        args,env=managed_app_server_spec(self.executable,self.environment,disabled_integrations=self.disabled_integrations)
        payload={'workspace':workspace,'args':args,'environment':env,'skills':skills,'model':model,'effort':'medium','git_write':self._engineering(run,role,workspace) and role=='implement'}
        return workspace,hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def _engineering(self,run,role,workspace):
        return bool(self.engineering_root and role in ('implement','test','qa') and workspace==str(Path(self.engineering_root)/'workspaces'/run/'repo'))
    def can_resume(self,run,role,session,skills,model):
        if role not in _WRITERS or not isinstance(session,str) or not session: return False
        try:
            _,binding=self._binding(run,role,skills,model)
            dbh=self._db()
            try:
                row=dbh.execute('SELECT session,binding,state FROM local_sessions WHERE run=? AND role=?',(run,role)).fetchone()
                live=dbh.execute("SELECT 1 FROM local_scopes WHERE run=? AND state IS NOT 'terminated'",(run,)).fetchone()
                return row==(session,binding,'idle') and live is None
            finally: dbh.close()
        except Exception: return False
    def start_execution(self,run,role,session,skills,model):
        if not self.preflight() or self.fence(run) is not True: raise LocalSupervisorError('local policy or run fence unavailable')
        workspace,binding=self._binding(run,role,skills,model)
        if session is not None:
            if not self.can_resume(run,role,session,skills,model): raise LocalSupervisorError('local session unavailable')
            dbh=self._db()
            try:
                if dbh.execute("UPDATE local_sessions SET state='active' WHERE run=? AND role=? AND session=? AND binding=? AND state='idle'",(run,role,session,binding)).rowcount!=1: raise LocalSupervisorError('local session already claimed')
            finally: dbh.close()
        elif role in _WRITERS:
            dbh=self._db()
            try: existing=dbh.execute('SELECT 1 FROM local_sessions WHERE run=? AND role=?',(run,role)).fetchone()
            finally: dbh.close()
            if existing: raise LocalSupervisorError('writer requires exact session resume')
        assignment=None
        if self._engineering(run,role,workspace):
            from runner.engineering_workspace import EngineeringWorkspace
            assignment='job-'+secrets.token_hex(24); registry=EngineeringWorkspace(self.engineering_root)
            try:
                if registry.claim(run,assignment,self.policy_hash)!=workspace: raise LocalSupervisorError('engineering workspace changed')
            finally:registry.close()
        handle=start_local_process(self.db,run,workspace,self.executable,self.environment,self.backend,lambda:self.fence(run),self.disabled_integrations,**({'allow_git':role=='implement'} if assignment else {}))
        if assignment is not None:
            registry=EngineeringWorkspace(self.engineering_root)
            try:
                try: registry.bind_local_scope(run,assignment,handle.unit)
                except Exception:
                    try:handle.cancel()
                    except Exception:pass
                    raise
            finally:registry.close()
        return LocalSessionHandle(self,handle,run,role,session,binding,assignment)

class LocalSessionHandle:
    def __init__(self,supervisor,handle,run,role,session,binding,assignment=None):
        self.supervisor,self.handle,self.run,self.role,self.session,self.binding=supervisor,handle,run,role,session,binding
        self.output_dir=handle.output_dir
        self.output_path_prefix=handle.output_path_prefix
        self.assignment=assignment
    def assert_active(self): return self.handle.assert_active()
    def open_transport(self,deadline,cancelled): return self.handle.open_transport(deadline,cancelled)
    def verify_config(self,request): return self.handle.verify_config(request)
    def bind_session(self,session):
        if self.role not in _WRITERS: return
        if not isinstance(session,str) or not session or (self.session is not None and session!=self.session): raise LocalSupervisorError('session identity mismatch')
        dbh=self.supervisor._db()
        try:
            if self.session is None:
                try: dbh.execute("INSERT INTO local_sessions VALUES(?,?,?,?, 'active')",(self.run,self.role,session,self.binding))
                except sqlite3.IntegrityError: raise LocalSupervisorError('session already bound') from None
                self.session=session
            elif not dbh.execute("SELECT 1 FROM local_sessions WHERE run=? AND role=? AND session=? AND binding=? AND state='active'",(self.run,self.role,session,self.binding)).fetchone(): raise LocalSupervisorError('session binding changed')
        finally: dbh.close()
    def finish(self):
        if self.handle.finish() is not True: raise LocalSupervisorError('local scope termination unverified')
        self._release_workspace()
        if self.role in _WRITERS and self.session is not None:
            dbh=self.supervisor._db()
            try:
                if dbh.execute("UPDATE local_sessions SET state='idle' WHERE run=? AND role=? AND session=? AND binding=? AND state='active'",(self.run,self.role,self.session,self.binding)).rowcount!=1: raise LocalSupervisorError('session termination binding unavailable')
            finally: dbh.close()
        return True
    def _release_workspace(self):
        if self.assignment is not None:
            from runner.engineering_workspace import EngineeringWorkspace
            registry=EngineeringWorkspace(self.supervisor.engineering_root)
            try:registry.release_local(self.run,self.assignment,self.handle.unit)
            finally:registry.close()
    def cancel(self):
        if self.handle.cancel() is not True: return False
        self._release_workspace(); return True
