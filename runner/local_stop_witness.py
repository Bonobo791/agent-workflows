"""Claim one local scope stop and record completion after its client exits."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys


def run_stop(path,run,unit,nonce):
    db=None
    try:
        state=Path(path);parent=state.parent;info=state.lstat();directory=parent.lstat()
        if (not state.is_absolute() or state.resolve(strict=True)!=state or parent.resolve(strict=True)!=parent
                or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022
                or not stat.S_ISDIR(directory.st_mode) or directory.st_uid!=os.geteuid() or directory.st_mode&0o077
                or not isinstance(run,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',run)
                or not isinstance(unit,str) or not re.fullmatch(r'agent-workflows-[0-9a-f]{48}\.scope',unit)
                or not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{64}',nonce)): return False
        digest=hashlib.sha256(bytes.fromhex(nonce)).hexdigest()
        db=sqlite3.connect(path,timeout=10,isolation_level=None)
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT o.invocation,o.cgroup FROM local_scope_stop_operations o JOIN local_scopes s ON s.run=o.run AND s.unit=o.unit AND s.invocation=o.invocation AND s.cgroup=o.cgroup WHERE o.run=? AND o.unit=? AND o.nonce_hash=? AND o.status='pending' AND s.state='stopping'",(run,unit,digest)).fetchone()
        if not row or not re.fullmatch(r'[0-9a-f]{32}',row[0]) or not isinstance(row[1],str) or not row[1].startswith('/') or not row[1].endswith('/'+unit) or '..' in Path(row[1]).parts: raise ValueError()
        if not db.execute("SELECT 1 FROM local_scope_stop_audit WHERE schema_version=1 AND run=? AND unit=? AND invocation=? AND cgroup=? AND nonce_hash=? AND status='pending'",(run,unit,*row,digest)).fetchone(): raise ValueError()
        last=db.execute('SELECT COALESCE(MAX(id),0) FROM local_scope_stop_audit').fetchone()[0]
        if db.execute("UPDATE local_scope_stop_operations SET status='active' WHERE run=? AND unit=? AND nonce_hash=? AND status='pending'",(run,unit,digest)).rowcount!=1: raise ValueError()
        if not db.execute("SELECT 1 FROM local_scope_stop_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND invocation=? AND cgroup=? AND nonce_hash=? AND status='active'",(last,run,unit,*row,digest)).fetchone(): raise ValueError()
        db.execute('COMMIT')
        result=subprocess.run(['/usr/bin/systemctl','--user','show',unit,'--property=Id','--property=LoadState','--property=ActiveState','--property=InvocationID','--property=ControlGroup'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=15,check=True)
        fields={}
        for line in result.stdout.splitlines():
            key,sep,value=line.partition('=')
            if not sep or key in fields: return False
            fields[key]=value
        if result.returncode!=0 or fields!={'Id':unit,'LoadState':'loaded','ActiveState':'active','InvocationID':row[0],'ControlGroup':row[1]}: return False
        result=subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=True)
        if result.returncode!=0:return False
        db.execute('BEGIN IMMEDIATE')
        last=db.execute('SELECT COALESCE(MAX(id),0) FROM local_scope_stop_audit').fetchone()[0]
        changed=db.execute("UPDATE local_scope_stop_operations SET status='completed' WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND nonce_hash=? AND status='active' AND EXISTS(SELECT 1 FROM local_scopes WHERE run=? AND unit=? AND invocation=? AND cgroup=? AND state='stopping')",(run,unit,*row,digest,run,unit,*row)).rowcount
        if changed!=1 or not db.execute("SELECT 1 FROM local_scope_stop_audit WHERE id>? AND schema_version=1 AND run=? AND unit=? AND invocation=? AND cgroup=? AND nonce_hash=? AND status='completed'",(last,run,unit,*row,digest)).fetchone(): raise ValueError()
        db.execute('COMMIT');return True
    except (OSError,ValueError,TypeError,sqlite3.Error,subprocess.SubprocessError):
        if db is not None and db.in_transaction:db.execute('ROLLBACK')
        return False
    finally:
        if db is not None:db.close()


if __name__=='__main__':
    try:
        raw=sys.stdin.buffer.read(4097)
        request=json.loads(raw) if len(raw)<=4096 else None
        if not isinstance(request,dict) or set(request)!={'path','run','unit','nonce'} or run_stop(**request) is not True:sys.exit(1)
    except (OSError,ValueError,TypeError):sys.exit(1)
