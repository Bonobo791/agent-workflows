"""Owner-only credential file storage with process-serialized updates (Unix)."""
import contextlib
import fcntl
import json
import os
import secrets
import stat

class CredentialError(RuntimeError):
    pass

class CredentialStore:
    def __init__(self, directory):
        self.directory = directory
        fd = self._directory()
        os.close(fd)

    def _directory(self):
        try: fd=os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError: raise CredentialError('private directory unavailable') from None
        info=os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            os.close(fd); raise CredentialError('directory must be owner-only')
        return fd

    @contextlib.contextmanager
    def locked(self):
        directory=self._directory()
        lock=None
        try:
            lock=os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            info=os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077: raise CredentialError('unsafe lock')
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield _Record(directory)
        except OSError:
            raise CredentialError('credential storage unavailable') from None
        finally:
            if lock is not None:
                fcntl.flock(lock, fcntl.LOCK_UN); os.close(lock)
            os.close(directory)

class _Record:
    def __init__(self, directory): self.directory=directory
    def load(self):
        try:
            fd=os.open('credentials.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.directory)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 65536: raise CredentialError('unsafe credential file')
                with os.fdopen(fd, 'r', encoding='utf-8', closefd=False) as file: return json.load(file)
            finally: os.close(fd)
        except (OSError, ValueError): raise CredentialError('credential file unavailable') from None
    def save(self, data):
        name='.credentials-' + secrets.token_hex(16)
        fd=None
        try:
            encoded=json.dumps(data, separators=(',',':')).encode('utf-8')
            if len(encoded)>65536: raise CredentialError('credential file too large')
            fd=os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
            with os.fdopen(fd, 'wb') as file:
                fd=None; file.write(encoded); file.flush(); os.fsync(file.fileno())
            os.replace(name, 'credentials.json', src_dir_fd=self.directory, dst_dir_fd=self.directory)
            os.fsync(self.directory)
        except (OSError, TypeError, ValueError): raise CredentialError('credential write failed') from None
        finally:
            if fd is not None: os.close(fd)
            try: os.unlink(name, dir_fd=self.directory)
            except FileNotFoundError: pass
