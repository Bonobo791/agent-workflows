"""Strict configuration fingerprint for a prospective Docker job boundary.

The hash identifies configuration; it is not itself evidence of approval or
host policy enforcement. This module does not enable execution.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

class ConfigError(ValueError): pass

class DockerConfig:
    FIELDS={'socket','image','network','job_root'}
    def __setattr__(self,name,value):
        if getattr(self,'_frozen',False): raise AttributeError('Docker configuration is immutable')
        object.__setattr__(self,name,value)
    def __init__(self,config,state_root):
        if not isinstance(config,dict) or set(config)!=self.FIELDS or any(not isinstance(x,str) or not x for x in config.values()): raise ConfigError('exact Docker configuration required')
        if not re.fullmatch(r'[A-Za-z0-9._/:@-]+@sha256:[0-9a-f]{64}',config['image']): raise ConfigError('pinned image required')
        if config['network']!='none' and not re.fullmatch(r'aw-[a-z0-9][a-z0-9-]{0,59}',config['network']): raise ConfigError('restricted network required')
        socket=config['socket']; root=Path(state_root); job=Path(config['job_root'])
        if not socket.startswith('/') or os.path.normpath(socket)!=socket or ':' in socket or '\x00' in socket: raise ConfigError('canonical Engine socket required')
        if not root.is_absolute() or not job.is_absolute() or job!=root/'jobs' or ':' in str(job): raise ConfigError('job root must be the fixed state jobs directory')
        try:
            if root.resolve(strict=True)!=root or job.resolve(strict=True)!=job: raise ConfigError('symlinked job root forbidden')
            for directory in (root,job):
                info=directory.stat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077: raise ConfigError('private owned job root required')
            if Path(socket).is_relative_to(job): raise ConfigError('Engine socket cannot be in job root')
        except OSError: raise ConfigError('job root unavailable') from None
        self.job_root=job; self.socket=socket; self.image=config['image']; self.network=config['network']
        canonical=json.dumps(config,sort_keys=True,separators=(',',':')).encode()
        self.policy_hash=hashlib.sha256(canonical).hexdigest()
        self._frozen=True
    def create_job_dirs(self,assignment):
        if not isinstance(assignment,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',assignment) or assignment in ('.','..'): raise ConfigError('invalid assignment')
        root=None; child=None
        try:
            root=os.open(self.job_root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            info=os.fstat(root)
            if info.st_uid!=os.geteuid() or info.st_mode & 0o077: raise ConfigError('job root unavailable')
            os.mkdir(assignment,0o700,dir_fd=root)
            child=os.open(assignment,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
            os.mkdir('workspace',0o700,dir_fd=child); os.mkdir('output',0o700,dir_fd=child)
            return {'workspace':str(self.job_root/assignment/'workspace'),'output':str(self.job_root/assignment/'output')}
        except OSError: raise ConfigError('job directory unavailable') from None
        finally:
            if child is not None: os.close(child)
            if root is not None: os.close(root)
