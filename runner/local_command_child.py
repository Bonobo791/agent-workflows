"""Trusted scope-side command relay; stdin and stdout are private pipes."""
import base64
import json
import os
import selectors
import subprocess
import sys
import time

MAX_LINE=2*1024*1024  # Matches runner.stdio.MAX_LINE for the isolated relay.

def emit(value):
    sys.stdout.write(json.dumps(value,separators=(',',':'))+'\n');sys.stdout.flush()

def main():
    emit({'ready':True})
    process=None
    try:
        line=sys.stdin.buffer.readline(MAX_LINE+1)
        if len(line)>MAX_LINE or not line.endswith(b'\n'): raise ValueError()
        request=json.loads(line)
        if not isinstance(request,dict) or set(request)-{'stdin_b64'}!={'argv','cwd','environment','timeout','output_limit'} or not isinstance(request['argv'],list) or not request['argv'] or any(not isinstance(x,str) or not x or '\x00' in x for x in request['argv']) or not isinstance(request['cwd'],str) or not request['cwd'].startswith('/') or not isinstance(request['environment'],dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in request['environment'].items()) or type(request['timeout']) is not int or not 1<=request['timeout']<=600 or type(request['output_limit']) is not int or not 1<=request['output_limit']<=1048576: raise ValueError()
        data=base64.b64decode(request['stdin_b64'],validate=True) if 'stdin_b64' in request else None
        if data is not None and len(data)>32768:raise ValueError()
        process=subprocess.Popen(request['argv'],cwd=request['cwd'],env=request['environment'],stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        if data is not None:
            view=memoryview(data)
            while view:
                written=process.stdin.write(view)
                if not written:raise OSError('command input closed')
                view=view[written:]
            process.stdin.close()
        selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ,'stdout');selector.register(process.stderr,selectors.EVENT_READ,'stderr')
        output={'stdout':bytearray(),'stderr':bytearray()};deadline=time.monotonic()+request['timeout']
        try:
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0: raise TimeoutError()
                for key,_ in selector.select(min(remaining,.1)):
                    data=os.read(key.fd,65536)
                    if not data: selector.unregister(key.fileobj);continue
                    target=output[key.data];target.extend(data)
                    if len(target)>request['output_limit']: raise ValueError()
            code=process.wait(timeout=max(.01,deadline-time.monotonic()))
        finally: selector.close()
        emit({'stdout':base64.b64encode(output['stdout']).decode('ascii')})
        emit({'stderr':base64.b64encode(output['stderr']).decode('ascii')})
        emit({'exit_code':code})
    except Exception:
        if process is not None and process.poll() is None:
            process.kill();process.wait()
        emit({'error':'command unavailable or limit exceeded'})
    sys.stdin.buffer.readline(MAX_LINE+1)

if __name__=='__main__': main()
