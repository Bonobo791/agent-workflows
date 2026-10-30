"""Synthetic protocol fixture; its file format is not a Codex storage format."""
import json
import os
from pathlib import Path
import sys
home=Path(os.environ['SYNTHETIC_STATE_HOME'])
for line in sys.stdin:
    message=json.loads(line); method=message.get('method'); ident=message.get('id')
    if method=='initialize': response={'id':ident,'result':{}}
    elif method=='initialized': continue
    elif method=='thread/start':
        (home/'synthetic-state').write_text('thread-one')
        response={'id':ident,'result':{'thread':{'id':'thread-one'}}}
    elif method=='thread/resume':
        thread=message['params']['threadId']
        if not (home/'synthetic-state').is_file() or (home/'synthetic-state').read_text()!=thread:
            response={'id':ident,'error':{'message':'state unavailable'}}
        else: response={'id':ident,'result':{'thread':{'id':thread}}}
    elif method=='turn/start':
        thread=message['params']['threadId']
        print(json.dumps({'id':ident,'result':{'turn':{'id':'turn-one'}}}),flush=True)
        response={'method':'turn/completed','params':{'threadId':thread,'turn':{'id':'turn-one','status':'completed'}}}
    else: continue
    print(json.dumps(response),flush=True)
