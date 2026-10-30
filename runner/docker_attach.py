"""NDJSON over an already hijacked Docker non-TTY attach stream.

The caller owns connection authentication, HTTP upgrade, exact identity checks,
container lifetime and cancellation fencing. Stderr is discarded, never logged.
"""
import json
import select
import struct
import time
from runner.stdio import MAX_LINE, StdioError

class DockerAttachTransport:
    def __init__(self,sock,deadline,cancelled):
        self.sock,self.deadline,self.cancelled=sock,deadline,cancelled
        self.wire=b''; self.stdout=b''
        sock.setblocking(False)
    def _wait(self,writing=False):
        while True:
            if self.cancelled(): raise StdioError('execution cancelled')
            remaining=self.deadline-time.monotonic()
            if remaining<=0: raise StdioError('execution timed out')
            readable,writable,_=select.select([] if writing else [self.sock],[self.sock] if writing else [],[],min(.1,remaining))
            if writable if writing else readable: return
    def send(self,message):
        try: data=json.dumps(message,separators=(',',':'),allow_nan=False).encode()+b'\n'
        except (TypeError,ValueError): raise StdioError('invalid request') from None
        if len(data)>MAX_LINE: raise StdioError('message too large')
        try:
            while data:
                self._wait(True); count=self.sock.send(data)
                if not count: raise StdioError('attach stream closed')
                data=data[count:]
        except OSError: raise StdioError('attach write failed') from None
    def _read(self,count):
        while len(self.wire)<count:
            self._wait()
            try: part=self.sock.recv(min(65536,count-len(self.wire)))
            except OSError: raise StdioError('attach read failed') from None
            if not part: raise StdioError('attach stream closed')
            self.wire+=part
        data,self.wire=self.wire[:count],self.wire[count:]
        return data
    def receive(self):
        while b'\n' not in self.stdout:
            header=self._read(8)
            channel,size=struct.unpack('>BxxxI',header)
            if channel not in (1,2) or header[1:4]!=b'\x00\x00\x00' or size>MAX_LINE: raise StdioError('invalid attach frame')
            payload=self._read(size)
            if channel==1:
                self.stdout+=payload
                if len(self.stdout)>MAX_LINE: raise StdioError('message too large')
        line,self.stdout=self.stdout.split(b'\n',1)
        try: result=json.loads(line)
        except (ValueError,UnicodeError): raise StdioError('invalid JSON') from None
        if not isinstance(result,dict): raise StdioError('invalid message')
        return result
