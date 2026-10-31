"""Bind app-server thread identity and loaded skill hashes to a trusted run."""
from runner.protocol import AppServer, ProtocolError
from workflows.marketing.gates import CHANNELS
import hashlib

MAX_OUTPUT=1048576

def run_codex(transport,provenance,run,role,claim,skills,prompt,model,session_id=None,review_subject=None,record_completion=True,before_turn=None,effort='medium',on_tool=None,before_thread=None,images=None):
    workflow=provenance.graph._definition(run).name
    resumable=(workflow=='engineering' and role=='implement') or (workflow=='marketing' and role in {'canonical_draft', *(c+'_draft' for c in CHANNELS)})
    if session_id:
        row=provenance.graph.db.execute('SELECT session FROM graph_sessions WHERE run=? AND role=?',(run,role)).fetchone()
        if not resumable or review_subject is not None or not row or row[0]!=session_id or not provenance.verify(run,role,session_id,skills):
            raise ProtocolError('session is not the recorded writer or implementer with matching skills')
    import json
    chunks=[]; thread=[None]; turn=[None]; early=[]; size=[0]; early_size=[0]; tools=[]; tool_size=[0]; tool_count=[0]; messages=[]; message_size=[0]
    def bind(actual):
        if session_id and actual != session_id: raise ProtocolError('resumed unexpected session')
        provenance.bind(run,role,actual,skills,claim,fresh=(session_id is None),subject=review_subject)
        if role == 'implement': provenance.graph.bind_session(run,role,actual,claim)
        thread[0]=actual
    def event(message):
        method=message.get('method'); params=message.get('params',{})
        if method not in ('item/agentMessage/delta','item/completed') or not isinstance(params,dict) or params.get('threadId')!=thread[0]: return
        if method=='item/agentMessage/delta' and not isinstance(params.get('delta'),str): return
        if method=='item/completed' and (not isinstance(params.get('item'),dict) or params['item'].get('type') not in ('webSearch','mcpToolCall','commandExecution','agentMessage')): return
        if turn[0] is None:
            early_size[0]+=len(json.dumps(message,ensure_ascii=False).encode('utf-8'))
            if len(early)>=10000 or early_size[0]>MAX_OUTPUT: raise ProtocolError('too many pending events')
            early.append(message); return
        if params.get('turnId') != turn[0]: return
        if method=='item/completed':
            item=params['item']
            if item['type']=='agentMessage':
                text=item.get('text');phase=item.get('phase')
                if not isinstance(text,str) or phase not in (None,'commentary','partial_answer','final_answer'): raise ProtocolError('invalid agent message')
                message_size[0]+=len(text.encode('utf-8'))
                if len(messages)>=1000 or message_size[0]>MAX_OUTPUT: raise ProtocolError('model output too large')
                messages.append((phase,text)); return
            tool_size[0]+=len(json.dumps(item,ensure_ascii=False).encode('utf-8'))
            tool_count[0]+=1
            if tool_count[0]>1000 or tool_size[0]>MAX_OUTPUT: raise ProtocolError('tool evidence too large')
            if on_tool is not None: on_tool(thread[0],turn[0],item)
            if item['type']!='commandExecution': tools.append(item)
            return
        text=params['delta']; size[0]+=len(text.encode('utf-8'))
        if size[0]>MAX_OUTPUT: raise ProtocolError('model output too large')
        chunks.append(text)
    def bind_turn(actual):
        turn[0]=actual
        for message in early: event(message)
        early.clear()
    actual=AppServer(transport,on_event=event,on_thread=bind,on_turn=bind_turn,before_turn=before_turn,before_thread=before_thread).run(prompt,model,thread_id=session_id,effort=effort,images=images)
    finals=[text for phase,text in messages if phase=='final_answer']
    if len(finals)>1: raise ProtocolError('multiple final agent messages')
    unknown=[text for phase,text in messages if phase is None]
    output=finals[0] if finals else unknown[-1] if unknown else '' if messages else ''.join(chunks)
    if record_completion: provenance.mark_completed(run,role,actual,claim,hashlib.sha256(output.encode('utf-8')).hexdigest())
    return {'session_id':actual,'turn_id':turn[0],'output':output,'tool_events':tools}
