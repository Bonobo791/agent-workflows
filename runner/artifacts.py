"""Private content-addressed blobs with verification on every read."""
import hashlib
import os
import re
import secrets
import stat

MAX_BYTES=100*1024*1024
class ArtifactError(RuntimeError):
    pass

class ArtifactStore:
    def __init__(self, directory): self.directory=directory
    def _dir(self):
        try: fd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        except OSError: raise ArtifactError('artifact directory unavailable') from None
        info=os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            os.close(fd); raise ArtifactError('artifact directory must be owner-only')
        return fd
    def put(self, data):
        if not isinstance(data,bytes) or len(data)>MAX_BYTES: raise ArtifactError('invalid artifact')
        digest=hashlib.sha256(data).hexdigest(); directory=self._dir(); name='.tmp-'+secrets.token_hex(16)
        try:
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400,dir_fd=directory)
            with os.fdopen(fd,'wb') as file: file.write(data); file.flush(); os.fsync(file.fileno())
            try: os.link(name,digest,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
            except FileExistsError: pass
            os.fsync(directory)
        except OSError: raise ArtifactError('artifact write failed') from None
        finally:
            try: os.unlink(name,dir_fd=directory)
            except FileNotFoundError: pass
            os.close(directory)
        if self.get(digest)!=data: raise ArtifactError('existing artifact does not match')
        return digest
    def get(self,digest):
        if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest): raise ArtifactError('invalid artifact reference')
        directory=self._dir()
        try:
            fd=os.open(digest,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size>MAX_BYTES: raise ArtifactError('unsafe artifact')
                with os.fdopen(fd,'rb',closefd=False) as file: data=file.read(MAX_BYTES+1)
                if len(data)>MAX_BYTES or hashlib.sha256(data).hexdigest()!=digest: raise ArtifactError('artifact integrity failure')
                return data
            finally: os.close(fd)
        except OSError: raise ArtifactError('artifact unavailable') from None
        finally: os.close(directory)
