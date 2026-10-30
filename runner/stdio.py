"""Bounded newline-delimited JSON transport over a supervised process's pipes.

Timeout, lifetime and process-tree cancellation are the supervisor's duties.
"""
import json

MAX_LINE=2*1024*1024
class StdioError(RuntimeError): pass

class NDJSONTransport:
    def __init__(self,reader,writer): self.reader,self.writer=reader,writer
    def send(self,message):
        try:
            data=json.dumps(message,separators=(',',':'),allow_nan=False).encode('utf-8')+b'\n'
            if len(data)>MAX_LINE: raise StdioError('message too large')
            self.writer.write(data); self.writer.flush()
        except (OSError,TypeError,ValueError): raise StdioError('transport write failed') from None
    def receive(self):
        try:
            data=self.reader.readline(MAX_LINE+1)
            if not data or len(data)>MAX_LINE or not data.endswith(b'\n'): raise StdioError('invalid transport frame')
            message=json.loads(data)
            if not isinstance(message,dict): raise StdioError('invalid transport message')
            return message
        except (OSError,TypeError,ValueError): raise StdioError('transport read failed') from None
