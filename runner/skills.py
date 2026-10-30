"""Resolve explicitly named local skill instructions without copying them."""
import hashlib
import re
from pathlib import Path

class SkillError(RuntimeError): pass

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
            try: data=matches[0].read_bytes(); content=data.decode('utf-8')
            except (OSError,UnicodeError): raise SkillError('skill unreadable: '+name) from None
            if len(data)>262144: raise SkillError('skill too large')
            result[name]={'sha256':hashlib.sha256(data).hexdigest(),'content':content}
        return result
