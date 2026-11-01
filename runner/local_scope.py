"""Durable identities and termination evidence for local systemd scopes.

A reserved scope is not proof of launch. Ambiguous starts and stops stay fenced.
"""
from pathlib import Path, PurePosixPath
import hashlib
import re
import secrets
import sqlite3
import subprocess

SCOPE_AUDIT_LAST='SELECT COALESCE(MAX(id),0) FROM local_scope_audit'
BEGIN_WRITE='BEGIN IMMEDIATE'
ASSIGNMENT_CHANGED='scope assignment changed'
TRANSITION_AUDIT_UNAVAILABLE='scope transition audit unavailable'

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

def reserve_scope(db,run):
    if not db.in_transaction: raise ScopeError('scope reservation transaction required')
    if not isinstance(run,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',run): raise ScopeError('invalid run')
    if unresolved_scope_rows(db,run): raise ScopeError('prior scope unresolved')
    unit='agent-workflows-'+secrets.token_hex(24)+'.scope'
    last=db.execute(SCOPE_AUDIT_LAST).fetchone()[0]
    db.execute("INSERT INTO local_scopes(run,unit,state) VALUES(?,?,'starting')",(run,unit))
    audit=db.execute("SELECT 1 FROM local_scope_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND state='starting'",(last,run,unit)).fetchone()
    if not audit: raise ScopeError('scope reservation audit unavailable')
    return unit

def reserve_for_execution(db,run,backend,workspace,workspace_claim=None):
    scopes=LocalScopes(db,backend)
    try:
        if workspace_claim is None: return scopes.reserve(run)
        from runner.engineering_workspace import EngineeringWorkspace
        if not isinstance(workspace_claim,tuple) or len(workspace_claim) not in (3,4) or Path(db).resolve()!=Path(workspace_claim[0]).resolve()/'state.db': raise ScopeError('workspace claim state mismatch')
        registry=EngineeringWorkspace(workspace_claim[0])
        try:
            _,unit=registry.claim_local(run,workspace_claim[1],workspace_claim[2],expected_path=str(workspace),pr_scope=workspace_claim[3] if len(workspace_claim)==4 else None)
            return unit
        finally: registry.close()
    finally: scopes.close()

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
    def stop_witnessed(self,path,run,unit,nonce):
        from runner.local_stop import start_stop_witness
        try:
            process=start_stop_witness(path,run,unit,nonce,self.environment)
            return process.wait(timeout=50)==0
        except (OSError,ValueError,subprocess.SubprocessError):return False
    def no_pending_job(self,unit):
        try:
            result=self._run('show',unit,'--property=Id','--property=Job').splitlines()
            return sorted(result)==sorted(['Id='+unit,'Job='])
        except ScopeError:return False
    def pid_in_cgroup(self,pid,cgroup):
        if type(pid) is not int or pid<=1 or not isinstance(cgroup,str) or not cgroup.startswith('/'): return False
        try:
            with (Path('/proc')/str(pid)/'cgroup').open() as stream: content=stream.read(65537)
            if len(content)>65536:return False
            return [line for line in content.splitlines() if line.startswith('0::')]==['0::'+cgroup]
        except (OSError,UnicodeError):return False
    def empty_cgroup(self,path):
        parts=PurePosixPath(path).parts
        if not path.startswith('/') or not parts or any(p in ('.','..') for p in parts): return False
        if not Path('/sys/fs/cgroup/cgroup.controllers').is_file(): return False
        target=Path('/sys/fs/cgroup').joinpath(*parts[1:])/'cgroup.events'
        try:
            values=dict(line.split() for line in target.read_text().splitlines())
            return values.get('populated')=='0'
        except FileNotFoundError:
            try: target.parent.stat()
            except FileNotFoundError: return Path('/sys/fs/cgroup/cgroup.controllers').is_file()
            except OSError: return False
            return False
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
        self.db.execute('CREATE TABLE IF NOT EXISTS local_scope_launch_receipts(run TEXT NOT NULL,unit TEXT PRIMARY KEY,invocation TEXT NOT NULL,cgroup TEXT NOT NULL,pid INTEGER NOT NULL,nonce_hash TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS local_scope_launch_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,unit TEXT NOT NULL,invocation TEXT NOT NULL,cgroup TEXT NOT NULL,pid INTEGER NOT NULL,nonce_hash TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS local_scope_recovery_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,unit TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS local_scope_stop_operations(run TEXT NOT NULL,unit TEXT PRIMARY KEY,invocation TEXT NOT NULL,cgroup TEXT NOT NULL,nonce_hash TEXT NOT NULL,status TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS local_scope_stop_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,unit TEXT NOT NULL,invocation TEXT NOT NULL,cgroup TEXT NOT NULL,nonce_hash TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS local_scope_stop_audit_insert AFTER INSERT ON local_scope_stop_operations BEGIN
            INSERT INTO local_scope_stop_audit(schema_version,run,unit,invocation,cgroup,nonce_hash,status,created) VALUES(1,NEW.run,NEW.unit,NEW.invocation,NEW.cgroup,NEW.nonce_hash,NEW.status,(julianday('now')-2440587.5)*86400.0); END""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS local_scope_stop_audit_update AFTER UPDATE OF status ON local_scope_stop_operations WHEN OLD.status != NEW.status BEGIN
            INSERT INTO local_scope_stop_audit(schema_version,run,unit,invocation,cgroup,nonce_hash,status,created) VALUES(1,NEW.run,NEW.unit,NEW.invocation,NEW.cgroup,NEW.nonce_hash,NEW.status,(julianday('now')-2440587.5)*86400.0); END""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS local_scope_launch_receipt_audit_insert AFTER INSERT ON local_scope_launch_receipts BEGIN
            INSERT INTO local_scope_launch_audit(schema_version,run,unit,invocation,cgroup,pid,nonce_hash,status,created) VALUES(1,NEW.run,NEW.unit,NEW.invocation,NEW.cgroup,NEW.pid,NEW.nonce_hash,'bound',(julianday('now')-2440587.5)*86400.0); END""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS local_scope_audit_insert AFTER INSERT ON local_scopes BEGIN
            INSERT INTO local_scope_audit(schema_version,run,unit,state,created) VALUES(1,NEW.run,NEW.unit,NEW.state,(julianday('now')-2440587.5)*86400.0); END""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS local_scope_audit_update AFTER UPDATE OF state ON local_scopes WHEN OLD.state != NEW.state BEGIN
            INSERT INTO local_scope_audit(schema_version,run,unit,state,created) VALUES(1,NEW.run,NEW.unit,NEW.state,(julianday('now')-2440587.5)*86400.0); END""")
    def close(self): self.db.close()
    def reserve(self,run):
        self.db.execute(BEGIN_WRITE)
        try:
            unit=reserve_scope(self.db,run)
            self.db.execute('COMMIT'); return unit
        except sqlite3.IntegrityError:
            self.db.execute('ROLLBACK'); raise ScopeError('scope reservation unavailable') from None
        except Exception:
            self.db.execute('ROLLBACK'); raise
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
    def _transition(self,run,unit,state,sql,params):
        self.db.execute(BEGIN_WRITE)
        try:
            last=self.db.execute(SCOPE_AUDIT_LAST).fetchone()[0]
            if self.db.execute(sql,params).rowcount!=1: raise ScopeError(ASSIGNMENT_CHANGED)
            event=self.db.execute('SELECT 1 FROM local_scope_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND state=?',(last,run,unit,state)).fetchone()
            if not event: raise ScopeError(TRANSITION_AUDIT_UNAVAILABLE)
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
    def bind(self,run,unit):
        info=self.backend.inspect(unit)
        group=info.get('cgroup','')
        if info.get('id')!=unit or info.get('load')!='loaded' or info.get('active')!='active' or not valid_scope_identity(unit,info.get('invocation'),group): raise ScopeError('active scope identity unverified')
        self._transition(run,unit,'running',"UPDATE local_scopes SET state='running',invocation=?,cgroup=? WHERE run=? AND unit=? AND state='starting'",(info['invocation'],group,run,unit))
    def bind_confirmed(self,run,unit,launch):
        from runner.local_launch import GuardedLaunch
        if (not isinstance(launch,GuardedLaunch) or launch.receipt!=(launch.process.pid,launch.nonce) or launch.go_fd is None
                or not isinstance(launch.nonce,str) or not re.fullmatch(r'[0-9a-f]{64}',launch.nonce)
                or type(launch.process.pid) is not int or launch.process.pid<=1 or launch.process.poll() is not None):
            raise ScopeError('launch confirmation unavailable')
        info=self.backend.inspect(unit);group=info.get('cgroup','')
        if (info.get('id')!=unit or info.get('load')!='loaded' or info.get('active')!='active'
                or not valid_scope_identity(unit,info.get('invocation'),group)
                or not callable(getattr(self.backend,'pid_in_cgroup',None))
                or self.backend.pid_in_cgroup(launch.process.pid,group) is not True
                or launch.process.poll() is not None): raise ScopeError('launch identity unverified')
        nonce_hash=hashlib.sha256(bytes.fromhex(launch.nonce)).hexdigest()
        self.db.execute(BEGIN_WRITE)
        try:
            scope_last=self.db.execute(SCOPE_AUDIT_LAST).fetchone()[0]
            launch_last=self.db.execute('SELECT COALESCE(MAX(id),0) FROM local_scope_launch_audit').fetchone()[0]
            if self.db.execute("UPDATE local_scopes SET state='running',invocation=?,cgroup=? WHERE run=? AND unit=? AND state='starting'",(info['invocation'],group,run,unit)).rowcount!=1: raise ScopeError(ASSIGNMENT_CHANGED)
            if not self.db.execute("SELECT 1 FROM local_scope_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND state='running'",(scope_last,run,unit)).fetchone(): raise ScopeError(TRANSITION_AUDIT_UNAVAILABLE)
            self.db.execute('INSERT INTO local_scope_launch_receipts VALUES(?,?,?,?,?,?)',(run,unit,info['invocation'],group,launch.process.pid,nonce_hash))
            if not self.db.execute("SELECT 1 FROM local_scope_launch_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND invocation=? AND cgroup=? AND pid=? AND nonce_hash=? AND status='bound'",(launch_last,run,unit,info['invocation'],group,launch.process.pid,nonce_hash)).fetchone(): raise ScopeError('launch confirmation audit unavailable')
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK');raise
    def _active_identity(self,run,unit):
        row=self.db.execute('SELECT invocation,cgroup,state FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone()
        if not row or row[2]!='running': raise ScopeError('running scope unavailable')
        info=self.backend.inspect(unit)
        if info.get('id')!=unit or info.get('load')!='loaded' or info.get('active')!='active' or info.get('invocation')!=row[0] or info.get('cgroup')!=row[1]: raise ScopeError('scope identity changed')
        return row
    def assert_active(self,run,unit):
        self._active_identity(run,unit)
        return True
    def release_confirmed(self,run,unit,launch,fence):
        from runner.local_launch import GuardedLaunch
        if not isinstance(launch,GuardedLaunch) or not callable(fence) or launch.receipt!=(launch.process.pid,launch.nonce): raise ScopeError('launch authorization unavailable')
        self.db.execute(BEGIN_WRITE)
        try:
            row=self.db.execute("SELECT r.pid,r.nonce_hash FROM local_scopes s JOIN local_scope_launch_receipts r ON r.run=s.run AND r.unit=s.unit AND r.invocation=s.invocation AND r.cgroup=s.cgroup JOIN local_scope_launch_audit a ON a.run=r.run AND a.unit=r.unit AND a.invocation=r.invocation AND a.cgroup=r.cgroup AND a.pid=r.pid AND a.nonce_hash=r.nonce_hash AND a.schema_version=1 AND a.status='bound' WHERE s.run=? AND s.unit=? AND s.state='running'",(run,unit)).fetchone()
            if not row or row!=(launch.process.pid,hashlib.sha256(bytes.fromhex(launch.nonce)).hexdigest()) or fence() is not True: raise ScopeError('launch authorization unavailable')
            launch.release()
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK');raise
    def _require_recovery_fence(self,run,fenced):
        if not isinstance(fenced,tuple) or len(fenced)!=2 or any(not isinstance(x,str) or not x for x in fenced): raise ScopeError('invalid recovery fence')
        fence=self.db.execute("SELECT 1 FROM manual_assignments a JOIN manual_workers w ON w.target=a.target JOIN jobs j ON j.run=a.run JOIN graph_runs g ON g.id=a.run WHERE a.run=? AND a.target=? AND a.incarnation=? AND a.state='pending' AND w.incarnation=a.incarnation AND w.state='assigned' AND j.status IN ('cancelling','uncertain') AND g.status='cancelled'",(run,*fenced)).fetchone()
        if not fence or self.db.execute('SELECT 1 FROM graph_claims WHERE run=?',(run,)).fetchone(): raise ScopeError('recovery fence changed')
    def _require_inactive(self,unit,invocation,cgroup):
        for _ in range(2):
            info=self.backend.inspect(unit)
            if (info.get('id')!=unit or info.get('active')!='inactive' or info.get('load') not in ('loaded','not-found')
                    or info.get('invocation') not in ('',invocation) or info.get('cgroup') not in ('',cgroup)
                    or self.backend.empty_cgroup(cgroup) is not True or self.backend.no_pending_job(unit) is not True):
                raise ScopeError('scope quiescence unverified')
    def reconcile_confirmed(self,run,unit,fenced):
        self.db.execute(BEGIN_WRITE)
        try:
            self._require_recovery_fence(run,fenced)
            row=self.db.execute("SELECT s.invocation,s.cgroup,r.pid,r.nonce_hash FROM local_scopes s JOIN local_scope_launch_receipts r ON r.run=s.run AND r.unit=s.unit AND r.invocation=s.invocation AND r.cgroup=s.cgroup WHERE s.run=? AND s.unit=? AND s.state='running'",(run,unit)).fetchone()
            if not row or not valid_scope_identity(unit,row[0],row[1]) or type(row[2]) is not int or row[2]<=1 or not isinstance(row[3],str) or not re.fullmatch(r'[0-9a-f]{64}',row[3]): raise ScopeError('confirmed launch unavailable')
            audit=self.db.execute("SELECT 1 FROM local_scope_launch_audit WHERE schema_version=1 AND run=? AND unit=? AND invocation=? AND cgroup=? AND pid=? AND nonce_hash=? AND status='bound'",(run,unit,*row)).fetchone()
            if not audit: raise ScopeError('launch audit unavailable')
            self._require_inactive(unit,*row[:2])
            last=self.db.execute(SCOPE_AUDIT_LAST).fetchone()[0]
            if self.db.execute("UPDATE local_scopes SET state='terminated' WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND state='running'",(run,unit,*row[:2])).rowcount!=1: raise ScopeError(ASSIGNMENT_CHANGED)
            if not self.db.execute("SELECT 1 FROM local_scope_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND state='terminated'",(last,run,unit)).fetchone(): raise ScopeError(TRANSITION_AUDIT_UNAVAILABLE)
            cursor=self.db.execute("INSERT INTO local_scope_recovery_audit(schema_version,run,unit,status,created) VALUES(1,?,?,'reconciled',(julianday('now')-2440587.5)*86400.0)",(run,unit))
            if cursor.rowcount!=1 or not self.db.execute("SELECT 1 FROM local_scope_recovery_audit WHERE id=? AND schema_version=1 AND run=? AND unit=? AND status='reconciled'",(cursor.lastrowid,run,unit)).fetchone(): raise ScopeError('recovery audit unavailable')
            self.db.execute('COMMIT');return True
        except Exception:
            self.db.execute('ROLLBACK');raise
    def reconcile_witnessed_stop(self,run,unit,fenced):
        self.db.execute(BEGIN_WRITE)
        try:
            self._require_recovery_fence(run,fenced)
            row=self._require_stop_receipt(run,unit)
            self._require_inactive(unit,row[0],row[1])
            last=self.db.execute(SCOPE_AUDIT_LAST).fetchone()[0]
            if self.db.execute("UPDATE local_scopes SET state='terminated' WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND state='stopping'",(run,unit,*row[:2])).rowcount!=1: raise ScopeError(ASSIGNMENT_CHANGED)
            if not self.db.execute("SELECT 1 FROM local_scope_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND state='terminated'",(last,run,unit)).fetchone(): raise ScopeError(TRANSITION_AUDIT_UNAVAILABLE)
            cursor=self.db.execute("INSERT INTO local_scope_recovery_audit(schema_version,run,unit,status,created) VALUES(1,?,?,'reconciled_stop',(julianday('now')-2440587.5)*86400.0)",(run,unit))
            if cursor.rowcount!=1 or not self.db.execute("SELECT 1 FROM local_scope_recovery_audit WHERE id=? AND schema_version=1 AND run=? AND unit=? AND status='reconciled_stop'",(cursor.lastrowid,run,unit)).fetchone(): raise ScopeError('recovery audit unavailable')
            self.db.execute('COMMIT');return True
        except Exception:
            self.db.execute('ROLLBACK');raise
    def _require_stop_receipt(self,run,unit):
        row=self.db.execute("SELECT o.invocation,o.cgroup,o.nonce_hash FROM local_scope_stop_operations o JOIN local_scopes s ON s.run=o.run AND s.unit=o.unit AND s.invocation=o.invocation AND s.cgroup=o.cgroup WHERE o.run=? AND o.unit=? AND o.status='completed' AND s.state='stopping'",(run,unit)).fetchone()
        if not row or not valid_scope_identity(unit,*row[:2]) or not isinstance(row[2],str) or not re.fullmatch(r'[0-9a-f]{64}',row[2]): raise ScopeError('completed stop unavailable')
        events=self.db.execute("SELECT status,id FROM local_scope_stop_audit WHERE schema_version=1 AND run=? AND unit=? AND invocation=? AND cgroup=? AND nonce_hash=? AND status IN ('pending','active','completed') ORDER BY id",(run,unit,*row)).fetchall()
        if [event[0] for event in events]!=['pending','active','completed']: raise ScopeError('stop audit unavailable')
        return row
    def stop(self,run,unit):
        row=self._active_identity(run,unit)
        witness=getattr(self.backend,'stop_witnessed',None)
        if callable(witness):
            nonce=self.reserve_stop(run,unit)
            try:
                path=self.db.execute('PRAGMA database_list').fetchone()[2]
                if witness(path,run,unit,nonce) is not True: raise ScopeError('witnessed stop uncertain')
                self.db.execute(BEGIN_WRITE)
                receipt=self._require_stop_receipt(run,unit)
                self._require_inactive(unit,*receipt[:2])
                last=self.db.execute(SCOPE_AUDIT_LAST).fetchone()[0]
                if self.db.execute("UPDATE local_scopes SET state='terminated' WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND state='stopping'",(run,unit,*receipt[:2])).rowcount!=1: raise ScopeError(ASSIGNMENT_CHANGED)
                if not self.db.execute("SELECT 1 FROM local_scope_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND state='terminated'",(last,run,unit)).fetchone(): raise ScopeError(TRANSITION_AUDIT_UNAVAILABLE)
                self.db.execute('COMMIT');return True
            except Exception:
                if self.db.in_transaction:self.db.execute('ROLLBACK')
                raise ScopeError('scope stop uncertain; run remains fenced') from None
        self._transition(run,unit,'stopping',"UPDATE local_scopes SET state='stopping' WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND state='running'",(run,unit,*row[:2]))
        try:
            if self.backend.stop(unit) is not True: raise ScopeError('scope stop uncertain')
            after=self.backend.inspect(unit)
            if after.get('id')!=unit or after.get('active')!='inactive' or after.get('load') not in ('loaded','not-found') or (after.get('load')=='loaded' and after.get('invocation') not in ('',row[0])) or not self.backend.empty_cgroup(row[1]): raise ScopeError('scope termination unverified')
            self._transition(run,unit,'terminated',"UPDATE local_scopes SET state='terminated' WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND state='stopping'",(run,unit,*row[:2]))
            return True
        except Exception: raise ScopeError('scope stop uncertain; run remains fenced') from None
    def reserve_stop(self,run,unit):
        self.assert_active(run,unit)
        nonce=secrets.token_hex(32);digest=hashlib.sha256(bytes.fromhex(nonce)).hexdigest()
        self.db.execute(BEGIN_WRITE)
        try:
            row=self.db.execute("SELECT invocation,cgroup FROM local_scopes WHERE run=? AND unit=? AND state='running'",(run,unit)).fetchone()
            if not row or not valid_scope_identity(unit,*row): raise ScopeError('scope identity unavailable')
            last=self.db.execute(SCOPE_AUDIT_LAST).fetchone()[0]
            if self.db.execute("UPDATE local_scopes SET state='stopping' WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND state='running'",(run,unit,*row)).rowcount!=1: raise ScopeError(ASSIGNMENT_CHANGED)
            if not self.db.execute("SELECT 1 FROM local_scope_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND state='stopping'",(last,run,unit)).fetchone(): raise ScopeError(TRANSITION_AUDIT_UNAVAILABLE)
            last=self.db.execute('SELECT COALESCE(MAX(id),0) FROM local_scope_stop_audit').fetchone()[0]
            self.db.execute("INSERT INTO local_scope_stop_operations VALUES(?,?,?,?,?,'pending')",(run,unit,*row,digest))
            if not self.db.execute("SELECT 1 FROM local_scope_stop_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND invocation=? AND cgroup=? AND nonce_hash=? AND status='pending'",(last,run,unit,*row,digest)).fetchone(): raise ScopeError('stop reservation audit unavailable')
            self.db.execute('COMMIT');return nonce
        except Exception:
            self.db.execute('ROLLBACK');raise
    def reconcile_stop(self,run,unit,verify_quiescent):
        row=self.db.execute('SELECT invocation,cgroup,state FROM local_scopes WHERE run=? AND unit=?',(run,unit)).fetchone()
        if not row or row[2]!='stopping' or not callable(verify_quiescent): raise ScopeError('uncertain scope stop unavailable')
        try:
            if verify_quiescent(run,unit,row[0],row[1]) is not True: raise ScopeError('stop request may still be in flight')
            info=self.backend.inspect(unit)
            if info.get('id')!=unit or info.get('active')!='inactive' or info.get('load') not in ('loaded','not-found') or (info.get('load')=='loaded' and info.get('invocation') not in ('',row[0])) or not self.backend.empty_cgroup(row[1]): raise ScopeError('scope termination unverified')
            if verify_quiescent(run,unit,row[0],row[1]) is not True: raise ScopeError('stop request may still be in flight')
            self._transition(run,unit,'terminated',"UPDATE local_scopes SET state='terminated' WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND state='stopping'",(run,unit,row[0],row[1]))
            return True
        except Exception: raise ScopeError('scope stop remains unresolved') from None
