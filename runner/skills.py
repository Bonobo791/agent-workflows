"""Resolve complete local skill packages and bind every file to their evidence."""
import base64
import hashlib
import json
import os
import re
import stat
from pathlib import Path

class SkillError(RuntimeError): pass

MAX_BYTES=262144

def _read(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise SkillError('unsupported skill file')
        data=stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES: raise SkillError('skill too large')
    return data

def _paths(directory):
    for item in directory.iterdir():
        yield item
        if not item.is_symlink() and item.is_dir(): yield from _paths(item)

def _package(path):
    main_data=_read(path); main=main_data.decode('utf-8'); files={}; bundled=[]; total=0
    for item in [path,*sorted(p for p in _paths(path.parent) if p!=path)]:
        if item.is_symlink(): raise SkillError('skill package symlink unavailable')
        if item.is_dir(): continue
        data=main_data if item==path else _read(item); total+=len(data)
        if total>MAX_BYTES: raise SkillError('skill package too large')
        name=item.relative_to(path.parent).as_posix(); digest=hashlib.sha256(data).hexdigest()
        files[name]=digest
        entry={'path':name,'sha256':digest}
        if item!=path:
            try: entry.update(encoding='utf-8',content=data.decode('utf-8'))
            except UnicodeDecodeError: entry.update(encoding='base64',content=base64.b64encode(data).decode('ascii'))
        bundled.append(entry)
    content=main
    if len(files)>1:
        content+='\n\nComplete bundled skill files; apply all relevant instructions:\n'+json.dumps({'files':bundled},sort_keys=True,ensure_ascii=False)
    if len(content.encode('utf-8'))>MAX_BYTES: raise SkillError('skill package too large')
    return {'sha256':hashlib.sha256(content.encode('utf-8')).hexdigest(),'content':content,'files':files}

class SkillResolver:
    def __init__(self,roots):
        self.roots=[Path(root).resolve(strict=True) for root in roots]
    def resolve(self,names):
        result={}
        for name in names:
            if not isinstance(name,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}',name): raise SkillError('invalid skill name')
            matches=[]
            for root in self.roots:
                try:
                    path=(root/name/'SKILL.md').resolve(strict=True)
                    if not path.is_relative_to(root) or not path.is_file(): continue
                    if path.stat().st_size>262144: raise SkillError('skill too large')
                    matches.append(path)
                except FileNotFoundError: continue
            if len(matches)!=1: raise SkillError('skill unavailable or ambiguous: '+name)
            try: result[name]=_package(matches[0])
            except (OSError,UnicodeError): raise SkillError('skill unreadable: '+name) from None
        return result
