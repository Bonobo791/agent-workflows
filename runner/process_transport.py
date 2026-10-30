"""Bounded subprocess transport. A trusted supervisor owns isolation and cleanup."""
import json
import os
import select
import time
from runner.stdio import MAX_LINE, StdioError

class ProcessTransport:
    def __init__(self,process,deadline,cancelled):
        self.process=process; self.deadline=deadline; self.cancelled=cancelled; self.buffer=b''
        if process.stdin is None or process.stdout is None: raise StdioError('process pipes unavailable')
        os.set_blocking(process.stdin.fileno(),False); os.set_blocking(process.stdout.fileno(),False)
    def _wait(self,fd,write=False):
        while True:
            if self.cancelled(): raise StdioError('execution cancelled')
            remaining=self.deadline-time.monotonic()
            if remaining<=0: raise StdioError('execution timed out')
            ready=select.select([] if write else [fd],[fd] if write else [],[],min(remaining,.1))
            if ready[1 if write else 0]: return
    def send(self,message):
        try: data=json.dumps(message,separators=(',',':'),allow_nan=False).encode()+b'\n'
        except (TypeError,ValueError): raise StdioError('invalid request') from None
        if len(data)>MAX_LINE: raise StdioError('message too large')
        fd=self.process.stdin.fileno()
        try:
            while data:
                self._wait(fd,True); data=data[os.write(fd,data):]
        except OSError: raise StdioError('process write failed') from None
    def receive(self):
        fd=self.process.stdout.fileno()
        while b'\n' not in self.buffer:
            if len(self.buffer)>MAX_LINE: raise StdioError('message too large')
            self._wait(fd)
            try: data=os.read(fd,65536)
            except OSError: raise StdioError('process read failed') from None
            if not data: raise StdioError('process closed output')
            self.buffer+=data
        line,self.buffer=self.buffer.split(b'\n',1)
        if len(line)+1>MAX_LINE: raise StdioError('message too large')
        try: message=json.loads(line)
        except (ValueError,UnicodeError): raise StdioError('invalid JSON') from None
        if not isinstance(message,dict): raise StdioError('invalid message')
        return message
