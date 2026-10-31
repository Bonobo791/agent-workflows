"""Publish a private, atomic Prometheus textfile on explicit invocation."""
import os
import fcntl
import secrets
import stat
import sys
from runner.metrics_cli import read_metrics
from runner.worker import ConfigurationError


def publish(directory):
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    temporary=None
    try:
        info=os.fstat(fd)
        if info.st_uid!=os.geteuid() or info.st_mode&0o077: raise ValueError('private metrics directory required')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        content=read_metrics().encode('utf-8')
        temporary='.workflow-'+secrets.token_hex(16)+'.tmp'
        output=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        with os.fdopen(output,'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,'workflow.prom',src_dir_fd=fd,dst_dir_fd=fd)
        temporary=None
        os.fsync(fd)
    finally:
        if temporary is not None:
            try: os.unlink(temporary,dir_fd=fd)
            except FileNotFoundError: pass
        os.close(fd)


def main():
    if len(sys.argv)!=2: raise ValueError('private metrics directory required')
    publish(sys.argv[1])


if __name__=='__main__':
    try: main()
    except (OSError,ValueError,ConfigurationError):
        print('Metrics publication failed.',file=sys.stderr)
        sys.exit(1)
