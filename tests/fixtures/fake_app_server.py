import json
import hashlib
import os
from pathlib import Path
import sys
import time

mode=sys.argv[1]
def send(value): print(json.dumps(value),flush=True)
for line in sys.stdin:
    message=json.loads(line); method=message.get('method'); ident=message.get('id')
    if method=='initialize': send({'id':ident,'result':{}})
    elif method in ('thread/start','thread/resume'):
        tid=message.get('params',{}).get('threadId') or os.environ.get('FAKE_THREAD','thread-one')
        send({'id':ident,'result':{'thread':{'id':tid}}})
    elif method=='turn/start':
        if mode=='invalid': print('{invalid',flush=True); break
        if mode=='exit': sys.exit(7)
        if mode=='hang': time.sleep(10); break
        tid=message['params']['threadId']; turn=os.environ.get('FAKE_TURN','turn-one')
        send({'id':ident,'result':{'turn':{'id':turn}}})
        if mode=='turn_exit': sys.exit(7)
        if mode=='turn_hang': time.sleep(30); break
        if mode=='approval': send({'id':99,'method':'item/commandExecution/requestApproval','params':{}}); continue
        if mode in ('tool','tool_exit'):
            send({'method':'item/completed','params':{'threadId':tid,'turnId':turn,'item':{'type':'mcpToolCall','status':'completed','arguments':{'input':'synthetic-private'},'result':{'output':'synthetic-private'}}}})
            if mode=='tool_exit': sys.exit(7)
        if mode=='command_exit':
            send({'method':'item/completed','params':{'threadId':tid,'turnId':turn,'item':{'type':'commandExecution','status':'completed','command':'synthetic-private-command','aggregatedOutput':'synthetic-private-output','exitCode':7}}})
            sys.exit(7)
        send({'method':'item/agentMessage/delta','params':{'threadId':tid,'turnId':'other','delta':'wrong'}})
        Path(os.environ['FAKE_OUTPUT'],'draft.txt').write_text('synthetic output')
        delta='correct'
        if os.environ.get('FAKE_ROLE')=='canonical_review':
            review={'stage':'canonical_review','status':'passed','input_versions':['subject-hash'],'outputs':[hashlib.sha256(b'synthetic output').hexdigest()],'findings':[]}
            if os.environ.get('FAKE_BLOCK_REVIEW')=='1':
                review.update(status='blocked',reason='correction required',findings=[{'id':'f1','severity':'blocking','description':'Missing source','location':'draft','evidence':'subject-hash','remedy':'Add source','resolution':'open'}])
            if os.environ.get('FAKE_BAD_REVIEW')=='1':
                review.update(status='blocked',reason='correction required',input_versions=['subject-hash','unbound'],findings=[{'id':'f1','severity':'blocking','description':'Missing source','location':'draft','evidence':'unbound','remedy':'Add source','resolution':'open'}])
            delta=json.dumps(review)
        send({'method':'item/agentMessage/delta','params':{'threadId':tid,'turnId':turn,'delta':delta}})
        send({'method':'turn/completed','params':{'threadId':tid,'turn':{'id':turn,'status':'completed'}}})
        if mode=='late-exit': sys.exit(7)
