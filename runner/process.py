"""Bounded cancellation for a local process group; this is not a sandbox."""
import os
import signal
import subprocess

class ManagedProcess:
    def __init__(self, argv, *, cwd, env):
        if not argv or not all(isinstance(arg,str) and arg for arg in argv): raise ValueError('argument vector required')
        self.process=subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      start_new_session=True, close_fds=True)
    def wait(self,timeout=None): return self.process.wait(timeout=timeout)
    def cancel(self,timeout=5):
        if self.process.poll() is not None: return
        try: os.killpg(self.process.pid,signal.SIGTERM)
        except ProcessLookupError: return
        try: self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try: os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            self.process.wait()
