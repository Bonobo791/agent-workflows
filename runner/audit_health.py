"""Private, bounded delivery-health snapshot for the authenticated metrics endpoint."""
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import stat
import time

NAME='audit-delivery-health.json'


def _directory(directory):
    path=Path(directory)
    if not path.is_absolute() or path.resolve(strict=True)!=path: raise ValueError('canonical private directory required')
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    info=os.fstat(fd)
    if info.st_uid!=os.geteuid() or info.st_mode&0o077:
        os.close(fd); raise ValueError('private directory required')
    return fd


def publish(directory,success,now=None):
    now=time.time() if now is None else now
    if type(success) is not bool or type(now) not in (int,float) or not math.isfinite(now) or now<0: raise ValueError('invalid delivery health')
    fd=_directory(directory); temporary=None
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        temporary='.audit-health-'+secrets.token_hex(16)+'.tmp'
        output=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        with os.fdopen(output,'w',encoding='utf-8') as stream:
            json.dump({'timestamp':int(now),'success':success},stream,separators=(',',':'))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,NAME,src_dir_fd=fd,dst_dir_fd=fd); temporary=None
        os.fsync(fd)
    finally:
        if temporary is not None:
            try: os.unlink(temporary,dir_fd=fd)
            except FileNotFoundError: pass
        os.close(fd)


def read_metrics(directory,now=None):
    result='workflow_audit_delivery_configured 1\n'
    now=time.time() if now is None else now
    try:
        directory_fd=_directory(directory)
        try:
            fd=os.open(NAME,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory_fd)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_nlink!=1 or info.st_size>1024: return result
                with os.fdopen(fd,'r',encoding='utf-8',closefd=False) as stream: value=json.load(stream)
            finally: os.close(fd)
        finally: os.close(directory_fd)
        if not isinstance(value,dict) or set(value)!={'timestamp','success'} or type(value['timestamp']) is not int or not 0<=value['timestamp']<=now+60 or type(value['success']) is not bool: return result
        return result+f"workflow_audit_delivery_last_attempt_timestamp_seconds {value['timestamp']}\nworkflow_audit_delivery_last_attempt_success {int(value['success'])}\n"
    except (OSError,ValueError,TypeError,UnicodeError): return result
