"""Fingerprint the exact private runtime trees used for offline audio review."""
import hashlib
import os
from pathlib import Path
import stat
import sys

class AudioRuntimeError(ValueError): pass

def fingerprint(roots):
    if not isinstance(roots,list) or len(roots)!=3 or any(not isinstance(root,str) for root in roots) or len(set(roots))!=3: raise AudioRuntimeError('three distinct runtime roots required')
    digest=hashlib.sha256();count=0;total=0;paths=[]
    try:
        for value in roots:
            root=Path(value);info=root.lstat()
            if not root.is_absolute() or root.resolve(strict=True)!=root or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022: raise ValueError()
            paths.append(root)
        if any(a!=b and (a.is_relative_to(b) or b.is_relative_to(a)) for a in paths for b in paths): raise ValueError()
        for root in paths:
            digest.update(b'root\0'+os.fsencode(root)+b'\0')
            def fail(error): raise error
            for current,dirs,files in os.walk(root,topdown=True,followlinks=False,onerror=fail):
                dirs.sort();files.sort()
                for name in sorted(dirs+files):
                    path=Path(current)/name;info=path.lstat();count+=1
                    if count>100000 or info.st_uid!=os.geteuid(): raise ValueError()
                    relative=os.fsencode(path.relative_to(root));digest.update(relative+b'\0')
                    if stat.S_ISLNK(info.st_mode):
                        digest.update(b'link\0'+os.fsencode(os.readlink(path))+b'\0')
                        if name in dirs:dirs.remove(name)
                    elif stat.S_ISDIR(info.st_mode):
                        if info.st_mode&0o022: raise ValueError()
                        digest.update(b'dir\0')
                    elif stat.S_ISREG(info.st_mode):
                        if info.st_mode&0o022: raise ValueError()
                        total+=info.st_size
                        if total>8*1024*1024*1024: raise ValueError()
                        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                        try:
                            actual=os.fstat(fd)
                            if (actual.st_dev,actual.st_ino,actual.st_size)!=(info.st_dev,info.st_ino,info.st_size): raise ValueError()
                            with os.fdopen(fd,'rb',closefd=False) as file: file_hash=hashlib.file_digest(file,'sha256').digest()
                        finally:os.close(fd)
                        digest.update(b'file\0'+str(info.st_mode&0o777).encode()+b'\0'+str(info.st_size).encode()+b'\0'+file_hash)
                    else: raise ValueError()
        return digest.hexdigest()
    except (OSError,TypeError,ValueError) as exc: raise AudioRuntimeError('audio runtime fingerprint unavailable') from exc

if __name__=='__main__':
    try:
        print(fingerprint(sys.argv[1:]))
    except AudioRuntimeError:
        print('Audio runtime fingerprint unavailable.',file=sys.stderr);sys.exit(1)
