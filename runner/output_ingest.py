"""Import declared regular files from an already quiesced private output tree."""
import os
from pathlib import PurePosixPath
import stat
from runner.artifacts import MAX_BYTES

class OutputError(RuntimeError): pass

def collect_outputs(directory, paths, store):
    if not isinstance(paths,list) or len(paths)>64 or any(not isinstance(p,str) for p in paths) or len(paths)!=len(set(paths)): raise OutputError('invalid output list')
    refs=[]
    try:
        root=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for path in paths:
                if not isinstance(path,str) or not path or '\\' in path or '\x00' in path: raise OutputError('invalid output path')
                parts=PurePosixPath(path).parts
                if not parts or path.startswith('/') or any(p in ('.','..') for p in path.split('/')): raise OutputError('invalid output path')
                current=os.dup(root)
                try:
                    for name in parts[:-1]:
                        child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current)
                        os.close(current); current=child
                    fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=current)
                    try:
                        info=os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_BYTES: raise OutputError('invalid output file')
                        with os.fdopen(fd,'rb',closefd=False) as stream: data=stream.read(MAX_BYTES+1)
                        if len(data)>MAX_BYTES: raise OutputError('output too large')
                        refs.append(store.put(data))
                    finally: os.close(fd)
                finally: os.close(current)
        finally: os.close(root)
    except (OSError,TypeError,ValueError): raise OutputError('output collection failed') from None
    return refs
