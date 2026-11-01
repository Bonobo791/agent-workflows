"""Hold a scoped command until its parent acknowledges the launch receipt."""
import fcntl
import json
import math
import os
import re
import select
import stat
import sys
import time


INVALID_HANDSHAKE='invalid launch handshake'

def main(argv):
    if len(argv)<6 or any(not value.isascii() or not value.isdecimal() for value in argv[1:3]):
        raise ValueError(INVALID_HANDSHAKE)
    ack_fd,go_fd=map(int,argv[1:3]);nonce=argv[3];deadline=float(argv[4]);target=argv[5]
    if min(ack_fd,go_fd)<3 or ack_fd==go_fd or not re.fullmatch(r'[0-9a-f]{64}',nonce) or not math.isfinite(deadline) or not os.path.isabs(target):
        raise ValueError(INVALID_HANDSHAKE)
    if not all(stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (ack_fd,go_fd)):
        raise ValueError(INVALID_HANDSHAKE)
    if (fcntl.fcntl(ack_fd,fcntl.F_GETFL)&os.O_ACCMODE)!=os.O_WRONLY or (fcntl.fcntl(go_fd,fcntl.F_GETFL)&os.O_ACCMODE)!=os.O_RDONLY:
        raise ValueError(INVALID_HANDSHAKE)
    receipt=json.dumps({'version':1,'nonce':nonce,'pid':os.getpid()},separators=(',',':')).encode()+b'\n'
    if os.write(ack_fd,receipt)!=len(receipt): raise OSError('incomplete launch receipt')
    os.close(ack_fd)
    remaining=deadline-time.monotonic()
    if remaining<=0 or not select.select([go_fd],[],[],remaining)[0] or os.read(go_fd,1)!=b'G' or time.monotonic()>=deadline:
        raise OSError('launch authorization unavailable')
    os.close(go_fd)
    # The trusted controller selects this argv and environment; no shell is used.
    os.execve(target,argv[5:],os.environ)


if __name__=='__main__':
    try: main(sys.argv)
    except (OSError,ValueError):
        print('Local launch unavailable.',file=sys.stderr)
        sys.exit(1)
