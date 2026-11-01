"""Durable binding for an already provisioned private engineering checkout.

The checkout is not cloned here. Provisioning and host isolation remain separate
operator actions. A job may claim only the exact registered task workspace.
"""
import os
import re
import sqlite3
import stat
import subprocess
from pathlib import Path

class WorkspaceError(RuntimeError): pass

def bind_scope_with_audit(db,run,assignment,unit):
    if not db.in_transaction: raise WorkspaceError('workspace binding transaction required')
    last=db.execute('SELECT COALESCE(MAX(id),0) FROM workspace_scope_audit').fetchone()[0]
    db.execute('INSERT INTO engineering_local_scopes VALUES(?,?,?)',(run,assignment,unit))
    row=db.execute("SELECT 1 FROM workspace_scope_audit WHERE id>? AND schema_version=1 AND run=? AND assignment=? AND unit=? AND status='bound'",(last,run,assignment,unit)).fetchone()
    if not row: raise WorkspaceError('workspace scope audit unavailable')

class EngineeringWorkspace:
    def __init__(self,root):
        self.root=Path(root)
        if not self.root.is_absolute() or self.root.resolve(strict=True)!=self.root: raise WorkspaceError('canonical state root required')
        self.allowed=self.root/'workspaces'
        info=self.allowed.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077 or self.allowed.resolve(strict=True)!=self.allowed: raise WorkspaceError('private workspace root required')
        self.db=sqlite3.connect(str(self.root/'state.db'),timeout=10,isolation_level=None)
        self.db.execute('CREATE TABLE IF NOT EXISTS engineering_workspaces(run TEXT PRIMARY KEY,repository TEXT NOT NULL,base TEXT NOT NULL,branch TEXT NOT NULL,owner INTEGER NOT NULL,path TEXT NOT NULL,policy TEXT NOT NULL,assignment TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS engineering_workspace_versions(run TEXT PRIMARY KEY,version INTEGER NOT NULL)')
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS engineering_workspace_version_update AFTER UPDATE ON engineering_workspaces BEGIN
            INSERT INTO engineering_workspace_versions VALUES(NEW.run,1) ON CONFLICT(run) DO UPDATE SET version=version+1; END""")
        self.db.execute('CREATE TABLE IF NOT EXISTS engineering_workspace_approvals(run TEXT PRIMARY KEY,binding_hash TEXT NOT NULL,actor TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS engineering_local_scopes(run TEXT NOT NULL,assignment TEXT PRIMARY KEY,unit TEXT NOT NULL UNIQUE)')
        self.db.execute('CREATE TABLE IF NOT EXISTS workspace_recovery_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,assignment TEXT NOT NULL,unit TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS workspace_scope_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,assignment TEXT NOT NULL,unit TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS workspace_scope_audit_insert AFTER INSERT ON engineering_local_scopes BEGIN
            INSERT INTO workspace_scope_audit(schema_version,run,assignment,unit,status,created) VALUES(1,NEW.run,NEW.assignment,NEW.unit,'bound',(julianday('now')-2440587.5)*86400.0); END""")
    def close(self): self.db.close()
    def _git(self,path,*args):
        try:
            result=subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',*args],cwd=path,env={'PATH':os.defpath,'HOME':'/nonexistent','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0'},check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
            return result.stdout.strip()
        except (OSError,subprocess.SubprocessError): raise WorkspaceError('workspace git verification failed') from None
    def verify(self,run,repository,base,branch,owner,policy,*,initial=False):
        if not all(isinstance(x,str) for x in (run,repository,base,branch,policy)) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',run) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',base) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',branch) or not re.fullmatch(r'[0-9a-f]{64}',policy) or type(owner) is not int or owner!=os.geteuid(): raise WorkspaceError('invalid workspace binding')
        path=self.allowed/run/'repo'
        try:
            for p in (self.allowed/run,path,path/'.git'):
                info=p.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner or info.st_mode & 0o077 or p.resolve(strict=True)!=p: raise WorkspaceError('unsafe workspace path or owner')
            if self._git(path,'rev-parse','--show-toplevel')!=str(path): raise WorkspaceError('workspace root mismatch')
            if self._git(path,'symbolic-ref','--short','HEAD')!=branch: raise WorkspaceError('workspace branch mismatch')
            if self._git(path,'config','--local','--get','remote.origin.url')!='https://github.com/'+repository+'.git': raise WorkspaceError('workspace repository mismatch')
            head=self._git(path,'rev-parse','--verify','HEAD^{commit}')
            if initial and head!=base: raise WorkspaceError('workspace base mismatch')
            self._git(path,'merge-base','--is-ancestor',base,head)
            if initial and self._git(path,'status','--porcelain','--untracked-files=all'): raise WorkspaceError('workspace not clean')
            return str(path)
        except OSError: raise WorkspaceError('workspace unavailable') from None
    def binding_hash(self,run,repository,base,branch,owner,policy):
        import hashlib, json
        data={'run':run,'repository':repository,'base':base,'branch':branch,'owner':owner,'path':str(self.allowed/run/'repo'),'policy':policy}
        return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def register(self,run,repository,base,branch,owner,policy,approval):
        if approval!=self.binding_hash(run,repository,base,branch,owner,policy): raise WorkspaceError('exact workspace approval required')
        path=self.verify(run,repository,base,branch,owner,policy,initial=True)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO engineering_workspaces VALUES(?,?,?,?,?,?,?,NULL)',(run,repository,base,branch,owner,path,policy))
            self.db.execute('INSERT INTO engineering_workspace_approvals VALUES(?,?,?)',(run,approval,'local-owner'))
            self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise WorkspaceError('workspace binding already exists') from None
        return path
    def _claim_binding(self,run,assignment,policy):
        if not re.fullmatch(r'job-[0-9a-f]{48}',assignment): raise WorkspaceError('invalid assignment')
        row=self.db.execute('SELECT repository,base,branch,owner,path,policy,assignment,COALESCE((SELECT version FROM engineering_workspace_versions v WHERE v.run=engineering_workspaces.run),0) FROM engineering_workspaces WHERE run=?',(run,)).fetchone()
        if not row or row[5]!=policy or row[6] is not None: raise WorkspaceError('workspace unavailable or already claimed')
        approved=self.db.execute('SELECT binding_hash FROM engineering_workspace_approvals WHERE run=?',(run,)).fetchone()
        if approved!=(self.binding_hash(run,*row[:4],policy),): raise WorkspaceError('workspace approval mismatch')
        return row
    def _verified_claim_binding(self,run,assignment,policy):
        row=self._claim_binding(run,assignment,policy)
        if self.verify(run,*row[:4],policy)!=row[4]: raise WorkspaceError('workspace binding changed')
        return row
    def _claim(self,run,assignment,policy,verified):
        row=self._claim_binding(run,assignment,policy)
        if row!=verified: raise WorkspaceError('workspace binding changed')
        self.db.execute('UPDATE engineering_workspaces SET assignment=? WHERE run=? AND assignment IS NULL',(assignment,run))
        return row[4]
    def claim(self,run,assignment,policy):
        verified=self._verified_claim_binding(run,assignment,policy)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            path=self._claim(run,assignment,policy,verified)
            self.db.execute('COMMIT'); return path
        except Exception: self.db.execute('ROLLBACK'); raise
    def claim_local(self,run,assignment,policy,*,expected_path=None,pr_scope=None):
        from runner.local_scope import reserve_scope
        verified=self._verified_claim_binding(run,assignment,policy)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            path=self._claim(run,assignment,policy,verified)
            if expected_path is not None and path!=expected_path: raise WorkspaceError('engineering workspace changed')
            unit=reserve_scope(self.db,run)
            bind_scope_with_audit(self.db,run,assignment,unit)
            if pr_scope is not None:
                if not isinstance(pr_scope,tuple) or len(pr_scope)!=3 or any(not isinstance(value,str) or not value for value in pr_scope) or pr_scope[2]!='bundle': raise WorkspaceError('invalid push scope binding')
                self.db.execute('INSERT INTO local_pr_scopes VALUES(?,?,?,?,?,?)',(unit,assignment,run,*pr_scope))
            self.db.execute('COMMIT'); return path,unit
        except Exception: self.db.execute('ROLLBACK'); raise
    def release(self,run,assignment):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT 1 FROM docker_assignments a JOIN docker_terminal_evidence e ON e.run=a.run AND e.container_id=a.container_id WHERE a.run=? AND a.state='terminated'",(assignment,)).fetchone()
            if not row: raise WorkspaceError('terminal container evidence required')
            if self.db.execute('UPDATE engineering_workspaces SET assignment=NULL WHERE run=? AND assignment=?',(run,assignment)).rowcount!=1: raise WorkspaceError('workspace claim mismatch')
            self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
    def bind_local_scope(self,run,assignment,unit):
        """Bind an older claim after observation; new local claims are already bound."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT 1 FROM engineering_workspaces w JOIN local_scopes s ON s.run=w.run WHERE w.run=? AND w.assignment=? AND s.unit=? AND s.state='running'",(run,assignment,unit)).fetchone()
            if not row: raise WorkspaceError('active local scope binding required')
            bind_scope_with_audit(self.db,run,assignment,unit); self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
    def release_local(self,run,assignment,unit,*,fenced=None):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if fenced is not None:
                if not isinstance(fenced,tuple) or len(fenced)!=2 or any(not isinstance(x,str) or not x for x in fenced): raise WorkspaceError('invalid recovery fence')
                fence=self.db.execute("SELECT 1 FROM manual_assignments a JOIN manual_workers w ON w.target=a.target JOIN jobs j ON j.run=a.run JOIN graph_runs g ON g.id=a.run WHERE a.run=? AND a.target=? AND a.incarnation=? AND a.state='pending' AND w.incarnation=a.incarnation AND w.state='assigned' AND ((j.status IN ('cancelling','uncertain') AND g.status='cancelled') OR (j.status='completed' AND g.status='completed'))",(run,*fenced)).fetchone()
                if not fence: raise WorkspaceError('recovery fence changed')
            from runner.local_scope import valid_scope_identity
            row=self.db.execute("SELECT s.invocation,s.cgroup FROM engineering_local_scopes m JOIN local_scopes s ON s.run=m.run AND s.unit=m.unit WHERE m.run=? AND m.assignment=? AND m.unit=? AND s.state='terminated'",(run,assignment,unit)).fetchone()
            if not row or not valid_scope_identity(unit,*row): raise WorkspaceError('terminal local scope evidence required')
            if self.db.execute('UPDATE engineering_workspaces SET assignment=NULL WHERE run=? AND assignment=?',(run,assignment)).rowcount!=1: raise WorkspaceError('workspace claim mismatch')
            if fenced is not None:
                self.db.execute("INSERT INTO workspace_recovery_audit(schema_version,run,assignment,unit,status,created) VALUES(1,?,?,?,'released',(julianday('now')-2440587.5)*86400.0)",(run,assignment,unit))
            self.db.execute('COMMIT')
        except Exception: self.db.execute('ROLLBACK'); raise
