"""Run an approved command in a managed local sandbox and scoped process tree."""
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from runner.adapter import managed_app_server_spec
from runner.local_scope import LocalScopes, reserve_for_execution
from runner.process_transport import ProcessTransport

class LocalCommandError(RuntimeError): pass

def run_scoped_sandbox(supervisor,run,workspace,argv,deadline,output_limit,on_reserved,*,output_dir=None,on_started=None,read_dirs=None,workspace_claim=None,reserved_command=None):
    s=supervisor
    if read_dirs is not None:
        try:
            work=Path(workspace)
            if not isinstance(read_dirs,list) or not work.is_absolute() or any(work.is_relative_to(Path(root)) or Path(root).is_relative_to(work) for root in read_dirs): raise ValueError()
        except (TypeError,ValueError): raise LocalCommandError('read roots must be separate from workspace') from None
    app,child_env=managed_app_server_spec(s.executable,s.environment,disabled_integrations=s.disabled_integrations,output_dir=output_dir,read_dirs=read_dirs)
    sandbox=[app[0],'sandbox','-C',workspace,'-P','workflow_local',*app[4:],'--',*argv]
    return _run_scoped(s,run,workspace,sandbox,child_env,deadline,output_limit,on_reserved,on_started,workspace_claim=workspace_claim,reserved_command=reserved_command)

def _run_scoped(s,run,workspace,argv,child_env,deadline,output_limit,on_reserved,on_started=None,*,input_data=None,workspace_claim=None,reserved_command=None):
    manager={k:s.environment[k] for k in ('HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if k in s.environment};manager['PATH']='/usr/bin:/bin'
    if reserved_command is None:
        unit=reserve_for_execution(s.db,run,s.backend,workspace,workspace_claim)
        on_reserved(unit)
    else:
        if workspace_claim is not None or not isinstance(reserved_command,tuple) or len(reserved_command)!=2: raise LocalCommandError('invalid command reservation')
        assignment,unit=reserved_command
        db=sqlite3.connect(s.db,timeout=10,isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute("SELECT 1 FROM local_command_scopes c JOIN local_scopes x ON x.run=c.run AND x.unit=c.unit JOIN engineering_local_scopes m ON m.run=c.run AND m.assignment=c.assignment AND m.unit=c.unit JOIN engineering_workspaces w ON w.run=c.run AND w.assignment=c.assignment WHERE c.assignment=? AND c.run=? AND c.unit=? AND c.exit_code IS NULL AND x.state='starting' AND w.path=?",(assignment,run,unit,workspace)).fetchone()
                if not row: raise LocalCommandError('command reservation unavailable')
                last=db.execute('SELECT COALESCE(MAX(id),0) FROM local_command_launch_audit').fetchone()[0]
                db.execute('INSERT INTO local_command_launches VALUES(?,?,?)',(assignment,run,unit))
                audit=db.execute("SELECT 1 FROM local_command_launch_audit WHERE id>? AND schema_version=1 AND run=? AND assignment=? AND unit=? AND status='starting'",(last,run,assignment,unit)).fetchone()
                if not audit: raise LocalCommandError('command launch audit unavailable')
                db.execute('COMMIT')
            except sqlite3.IntegrityError:
                db.execute('ROLLBACK'); raise LocalCommandError('command launch unavailable') from None
            except Exception:
                db.execute('ROLLBACK'); raise
        finally: db.close()

    relay=str(Path(__file__).with_name('local_command_child.py').resolve(strict=True))
    command=['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--','/usr/bin/env','-i']+[k+'='+v for k,v in child_env.items()]+['/usr/bin/python3','-I',relay]
    process=None; stopped=False
    try:
        process=subprocess.Popen(command,cwd=workspace,env=manager,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
        transport=ProcessTransport(process,deadline,lambda:s.fence(run) is not True)
        if transport.receive()!={'ready':True}: raise LocalCommandError('scope relay unavailable')
        scopes=LocalScopes(s.db,s.backend)
        try:scopes.bind(run,unit)
        finally:scopes.close()
        if on_started is not None:on_started(unit)
        if s.fence(run) is not True: raise LocalCommandError('run fence unavailable')
        remaining=int(deadline-time.monotonic())-1
        if remaining<1: raise LocalCommandError('command deadline unavailable')
        request={'argv':argv,'cwd':workspace,'environment':child_env,'timeout':remaining,'output_limit':output_limit}
        if input_data is not None:
            if not isinstance(input_data,bytes) or len(input_data)>32768:raise LocalCommandError('command input invalid')
            request['stdin_b64']=base64.b64encode(input_data).decode('ascii')
        transport.send(request)
        out=transport.receive()
        if set(out)!={'stdout'}: raise LocalCommandError('command result unavailable')
        err=transport.receive()
        if set(err)!={'stderr'}: raise LocalCommandError('command result unavailable')
        result=transport.receive()
        if set(result)!={'exit_code'} or type(result['exit_code']) is not int: raise LocalCommandError('command result unavailable')
        stdout=base64.b64decode(out['stdout'],validate=True);stderr=base64.b64decode(err['stderr'],validate=True)
        if len(stdout)>output_limit or len(stderr)>output_limit or s.fence(run) is not True: raise LocalCommandError('command evidence unavailable')
        scopes=LocalScopes(s.db,s.backend)
        try: stopped=scopes.stop(run,unit) is True
        finally:scopes.close()
        if not stopped: raise LocalCommandError('scope termination unavailable')
        process.wait(timeout=max(.01,deadline-time.monotonic()))
        return {'outcome':'exited','exit_code':result['exit_code'],'stdout':stdout,'stderr':stderr,'unit':unit}
    except Exception: raise LocalCommandError('local command outcome uncertain') from None
    finally:
        if process is not None:
            if not stopped:
                try:
                    scopes=LocalScopes(s.db,s.backend)
                    try:scopes.stop(run,unit)
                    finally:scopes.close()
                except Exception:pass
            for stream in (process.stdin,process.stdout):
                if stream is not None:stream.close()

class LocalCommandBackend:
    def __init__(self,supervisor):
        self.supervisor=supervisor
        db=self._db()
        try:
            db.execute('CREATE TABLE IF NOT EXISTS local_command_scopes(assignment TEXT PRIMARY KEY,run TEXT NOT NULL,unit TEXT NOT NULL UNIQUE,exit_code INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS local_command_launches(assignment TEXT PRIMARY KEY,run TEXT NOT NULL,unit TEXT NOT NULL UNIQUE)')
            db.execute('CREATE TABLE IF NOT EXISTS local_command_launch_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,assignment TEXT NOT NULL,unit TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
            db.execute("""CREATE TRIGGER IF NOT EXISTS local_command_launch_audit_insert AFTER INSERT ON local_command_launches BEGIN
                INSERT INTO local_command_launch_audit(schema_version,run,assignment,unit,status,created) VALUES(1,NEW.run,NEW.assignment,NEW.unit,'starting',(julianday('now')-2440587.5)*86400.0); END""")
        finally:db.close()
    def _db(self): return sqlite3.connect(self.supervisor.db,timeout=10,isolation_level=None)
    def reserve_local_scope(self,db,assignment,run):
        from runner.local_scope import reserve_scope
        from runner.engineering_workspace import bind_scope_with_audit
        unit=reserve_scope(db,run)
        bind_scope_with_audit(db,run,assignment,unit)
        db.execute('INSERT INTO local_command_scopes(assignment,run,unit) VALUES(?,?,?)',(assignment,run,unit))
    def verify_terminal(self,assignment,run,exit_code):
        if type(exit_code) is not int:return False
        from runner.local_scope import valid_scope_identity
        db=self._db()
        try:
            row=db.execute("SELECT c.unit,s.invocation,s.cgroup FROM local_command_scopes c JOIN local_scopes s ON s.run=c.run AND s.unit=c.unit WHERE c.assignment=? AND c.run=? AND c.exit_code=? AND s.state='terminated'",(assignment,run,exit_code)).fetchone()
            return bool(row and valid_scope_identity(*row))
        finally:db.close()
    def execute(self,*,assignment,run,workspace,commit,argv,timeout,output_limit):
        s=self.supervisor; deadline=time.monotonic()+timeout
        if not s.preflight() or s.fence(run) is not True: raise LocalCommandError('local runtime unavailable')
        spec={'argv':argv,'timeout_seconds':timeout,'output_limit':output_limit}
        policy=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        db=self._db()
        try:
            row=db.execute('SELECT run,commit_id,policy_hash,state FROM supervised_commands WHERE id=?',(assignment,)).fetchone()
            bound=db.execute('SELECT path,policy,assignment FROM engineering_workspaces WHERE run=?',(run,)).fetchone()
            approved=db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='command_policy' AND version=? AND status='approved'",(policy,)).fetchone()
            if row!=(run,commit,policy,'uncertain') or bound!=(workspace,s.policy_hash,assignment) or not approved: raise LocalCommandError('exact command and workspace approval required')
        finally:db.close()
        db=self._db()
        try: row=db.execute('SELECT unit FROM local_command_scopes WHERE assignment=? AND run=? AND exit_code IS NULL',(assignment,run)).fetchone()
        finally: db.close()
        if not row: raise LocalCommandError('command reservation unavailable')
        result=run_scoped_sandbox(s,run,workspace,argv,deadline,output_limit,lambda _:None,reserved_command=(assignment,row[0]))
        unit=result.pop('unit')
        db=self._db()
        try:
            if db.execute('UPDATE local_command_scopes SET exit_code=? WHERE assignment=? AND run=? AND unit=? AND exit_code IS NULL',(result['exit_code'],assignment,run,unit)).rowcount!=1: raise LocalCommandError('command scope binding changed')
        finally:db.close()
        return result
