"""Private application-owned state allocation; not a Codex auth boundary.

No existing Codex home is read or copied. A successful filename scan is not
proof that arbitrary session contents contain no credentials. Production mount
and resume remain disabled until the credential boundary is independently
verified for the actual runtime.
"""
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat

class SessionError(RuntimeError): pass

_WRITERS={'implement','canonical_draft','linkedin_draft','x_draft','facebook_draft','instagram_draft','reddit_draft'}
_FORBIDDEN=re.compile(r'(?i)(?:\.env(?:\..*)?|auth(?:entication)?(?:[._-].*)?|config\.toml|.*(?:token|credential|secret|private[-_]?key).*|.*\.(?:pem|key|p12))$')

def _name(value):
    return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',value) and value not in ('.','..')
def _digest(value): return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value)

class SessionStore:
    def __init__(self,root):
        self.root=Path(root)
        try:
            info=self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or self.root.resolve(strict=True)!=self.root: raise SessionError('private state root required')
            self.homes=self.root/'codex-homes'
            self.homes.mkdir(mode=0o700,exist_ok=True)
            info=self.homes.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077: raise SessionError('unsafe homes root')
        except OSError: raise SessionError('state root unavailable') from None
        try:
            fd=os.open(self.root/'session-state.db',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077: raise SessionError('unsafe state database')
            finally: os.close(fd)
        except OSError: raise SessionError('state database unavailable') from None
        self.db=sqlite3.connect(str(self.root/'session-state.db'),timeout=10,isolation_level=None)
        self.db.execute('''CREATE TABLE IF NOT EXISTS homes(id TEXT PRIMARY KEY,run TEXT NOT NULL,role TEXT NOT NULL,config TEXT NOT NULL,skills TEXT NOT NULL,session TEXT UNIQUE,state TEXT NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS home_claims(job TEXT PRIMARY KEY,home TEXT NOT NULL,state TEXT NOT NULL)''')
        self.db.execute('CREATE TABLE IF NOT EXISTS backup_generations(home TEXT PRIMARY KEY,generation TEXT NOT NULL)')
    def close(self): self.db.close()
    def acquire(self,run,role,job,config,skills,session=None,verify_safe=None):
        if not all(_name(x) for x in (run,role,job)) or not _digest(config) or not _digest(skills) or (session is not None and not _name(session)): raise SessionError('invalid session identity')
        if session is not None and role not in _WRITERS: raise SessionError('reviewers cannot resume')
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            if db.execute('SELECT 1 FROM home_claims WHERE job=?',(job,)).fetchone(): raise SessionError('assignment already used')
            if session is not None:
                row=db.execute('SELECT id,run,role,config,skills,state FROM homes WHERE session=?',(session,)).fetchone()
                if not row or row[1:]!=(run,role,config,skills,'idle'): raise SessionError('session binding unavailable')
                home=row[0]
                self._check_home(home)
                if not callable(verify_safe) or verify_safe(str(self.homes/home)) is not True: raise SessionError('credential boundary unverified')
                db.execute("UPDATE homes SET state='active' WHERE id=? AND state='idle'",(home,))
            else:
                if role in _WRITERS and db.execute('SELECT 1 FROM homes WHERE run=? AND role=?',(run,role)).fetchone(): raise SessionError('existing writer requires exact resume')
                home='home-'+secrets.token_hex(24)
                fd=os.open(self.homes,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try: os.mkdir(home,0o700,dir_fd=fd)
                finally: os.close(fd)
                db.execute("INSERT INTO homes VALUES(?,?,?,?,?,NULL,'active')",(home,run,role,config,skills))
            db.execute("INSERT INTO home_claims VALUES(?,?,'active')",(job,home)); db.execute('COMMIT')
            return {'home':str(self.homes/home),'session':session}
        except Exception:
            db.execute('ROLLBACK'); raise
    def can_resume(self,run,role,session,config,skills,verify_safe):
        if role not in _WRITERS or not all(_name(x) for x in (run,role,session)) or not _digest(config) or not _digest(skills) or not callable(verify_safe): return False
        try:
            row=self.db.execute('SELECT id,run,role,config,skills,state FROM homes WHERE session=?',(session,)).fetchone()
            if not row or row[1:]!=(run,role,config,skills,'idle'): return False
            self._check_home(row[0])
            return verify_safe(str(self.homes/row[0])) is True
        except Exception: return False
    def bind(self,job,session):
        if not _name(job) or not _name(session): raise SessionError('invalid session identity')
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            row=db.execute("SELECT h.id,h.session FROM homes h JOIN home_claims c ON c.home=h.id WHERE c.job=? AND c.state='active' AND h.state='active'",(job,)).fetchone()
            if not row or (row[1] is not None and row[1]!=session): raise SessionError('session assignment unavailable')
            try: db.execute('UPDATE homes SET session=? WHERE id=?',(session,row[0]))
            except sqlite3.IntegrityError: raise SessionError('session belongs to another home') from None
            db.execute('COMMIT')
        except Exception: db.execute('ROLLBACK'); raise
    def _check_home(self,home,fd=None):
        path=self.homes/home if fd is None else Path(f'/proc/{os.getpid()}/fd/{fd}')
        try:
            info=path.lstat() if fd is None else os.fstat(fd)
            identity=(info.st_dev,info.st_ino)
            if (fd is None and path.resolve(strict=True)!=path) or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077: raise SessionError('unsafe state home')
            def scan_error(error): raise error
            for directory,dirs,files in os.walk(path,followlinks=False,onerror=scan_error):
                for name in dirs+files:
                    if _FORBIDDEN.fullmatch(name): raise SessionError('forbidden state entry')
                    info=os.lstat(Path(directory)/name)
                    if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1): raise SessionError('unsafe state entry')
            return identity
        except OSError: raise SessionError('state unavailable') from None
    def release(self,job,verify_stopped):
        if not callable(verify_stopped) or verify_stopped(job) is not True: raise SessionError('writer termination unverified')
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            row=db.execute("SELECT home FROM home_claims WHERE job=? AND state='active'",(job,)).fetchone()
            if not row: raise SessionError('active assignment unavailable')
            self._check_home(row[0])
            db.execute("UPDATE homes SET state='idle' WHERE id=? AND state='active'",(row[0],))
            db.execute("UPDATE home_claims SET state='released' WHERE job=?",(job,)); db.execute('COMMIT')
        except Exception: db.execute('ROLLBACK'); raise
    def _reserve_idle(self,job,state):
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            row=db.execute("SELECT h.id FROM homes h JOIN home_claims c ON c.home=h.id WHERE c.job=? AND c.state='released' AND h.state='idle'",(job,)).fetchone()
            if not row: raise SessionError('idle state unavailable')
            identity=self._check_home(row[0])
            db.execute("UPDATE homes SET state=? WHERE id=? AND state='idle'",(state,row[0]))
            generation=secrets.token_hex(24) if state=='backup' else None
            if generation is not None: db.execute('INSERT OR REPLACE INTO backup_generations VALUES(?,?)',(row[0],generation))
            db.execute('COMMIT'); return (row[0],generation,identity) if generation is not None else row[0]
        except Exception: db.execute('ROLLBACK'); raise
    def backup(self,job,exporter,verify_safe=None):
        if not callable(exporter) or not callable(verify_safe): raise SessionError('trusted export and credential verification required')
        home,generation,identity=self._reserve_idle(job,'backup')
        original=self.homes/home
        try: fd=os.open(original,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        except OSError: raise SessionError('backup home unavailable') from None
        try:
            info=os.fstat(fd)
            if (info.st_dev,info.st_ino)!=identity:raise SessionError('backup home changed')
            self._check_home(home,fd)
            path=f'/proc/{os.getpid()}/fd/{fd}'
            try: verified=verify_safe(path) is True
            except Exception: verified=False
            if not verified:
                self._finish_backup(home,generation)
                raise SessionError('credential boundary unverified')
            try: result=exporter(path,generation)
            except Exception: raise SessionError('backup outcome unresolved') from None
            try: current=original.lstat()
            except OSError: raise SessionError('backup home changed') from None
            if (current.st_dev,current.st_ino)!=identity:raise SessionError('backup home changed')
            self._finish_backup(home,generation)
            return result
        finally: os.close(fd)
    def _finish_backup(self,home,generation):
        changed=self.db.execute("UPDATE homes SET state='idle' WHERE id=? AND state='backup' AND EXISTS(SELECT 1 FROM backup_generations WHERE home=? AND generation=?)",(home,home,generation)).rowcount
        if changed!=1: raise SessionError('backup generation changed')
    def recover_backup(self,job,verify_quiescent,verify_safe):
        if not _name(job) or not callable(verify_quiescent) or not callable(verify_safe): raise SessionError('trusted backup verification required')
        row=self.db.execute("SELECT h.id,g.generation FROM homes h JOIN home_claims c ON c.home=h.id JOIN backup_generations g ON g.home=h.id WHERE c.job=? AND c.state='released' AND h.state='backup'",(job,)).fetchone()
        if not row: raise SessionError('backup recovery unavailable')
        home,generation=row; path=str(self.homes/home)
        try:
            if verify_quiescent(job,path,generation) is not True or verify_safe(path) is not True: raise SessionError('backup recovery unverified')
        except Exception: raise SessionError('backup recovery unverified') from None
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            self._check_home(home)
            changed=db.execute("UPDATE homes SET state='idle' WHERE id=? AND state='backup' AND EXISTS(SELECT 1 FROM home_claims WHERE job=? AND home=? AND state='released') AND EXISTS(SELECT 1 FROM backup_generations WHERE home=? AND generation=?)",(home,job,home,home,generation)).rowcount
            if changed!=1: raise SessionError('backup recovery stale')
            db.execute('COMMIT'); return True
        except Exception: db.execute('ROLLBACK'); raise
    def delete(self,job,deleter):
        if not callable(deleter): raise SessionError('trusted deletion required')
        home=self._reserve_idle(job,'deleting'); path=self.homes/home
        try:
            if deleter(str(path)) is not True or path.exists() or path.is_symlink(): raise SessionError('deletion unverified')
            self.db.execute("UPDATE homes SET state='deleted' WHERE id=? AND state='deleting'",(home,))
            return True
        except Exception: raise SessionError('deletion outcome unresolved') from None
