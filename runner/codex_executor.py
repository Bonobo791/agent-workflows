"""Launch through a trusted isolation supervisor; no default host launcher."""
import hashlib
import json
import secrets
import time
import threading
from runner.codex_session import run_codex
from runner.output_ingest import collect_outputs
from runner.process_transport import ProcessTransport
from runner.structured import parse_marketing_result

class ExecutorError(RuntimeError): pass
class UncertainExecutorError(ExecutorError): pass

def _local_prompt(prompt,output_dir,output_paths,instruction_length):
    if not isinstance(prompt,str) or type(instruction_length) is not int or not 0<instruction_length<len(prompt): raise ExecutorError('local output instructions unavailable')
    instructions=prompt[:instruction_length]
    if output_paths and '/output/' not in instructions: raise ExecutorError('local output instructions unavailable')
    return instructions.replace('/output/',output_dir+'/')+prompt[instruction_length:]

def _bounded(call,deadline,cancelled,late_cleanup=None):
    done=threading.Event(); lock=threading.Lock(); state={'abandoned':False}; result=[]
    def invoke():
        try: value=call(); outcome=(True,value)
        except Exception as error: outcome=(False,error)
        with lock:
            if state['abandoned']:
                if outcome[0] and late_cleanup:
                    try: late_cleanup(outcome[1])
                    except Exception: pass
            else: result.append(outcome)
        done.set()
    threading.Thread(target=invoke,daemon=True).start()
    while not done.wait(.05):
        if cancelled() or time.monotonic()>=deadline:
            with lock:
                if result: break
                state['abandoned']=True
            raise UncertainExecutorError('supervisor operation timed out or cancelled; outcome uncertain')
    if not result or not result[0][0]: raise ExecutorError('supervisor operation failed') from None
    return result[0][1]

class CodexExecutor:
    def __init__(self, supervisor, provenance, artifacts, fence,model_policy=None):
        self.supervisor=supervisor; self.provenance=provenance; self.artifacts=artifacts; self.fence=fence; self.model_policy=model_policy
        provenance.graph.db.execute('''CREATE TABLE IF NOT EXISTS completed_turn_outputs(
            run TEXT NOT NULL, role TEXT NOT NULL, session TEXT NOT NULL, turn TEXT NOT NULL,
            input_versions TEXT NOT NULL, output_refs TEXT NOT NULL,
            PRIMARY KEY(run,role,session,turn))''')
        provenance.graph.db.execute('''CREATE TABLE IF NOT EXISTS execution_turn_audit(
            id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, run TEXT NOT NULL, role TEXT NOT NULL,
            session TEXT NOT NULL, turn TEXT NOT NULL, status TEXT NOT NULL, model TEXT NOT NULL,
            input_versions TEXT NOT NULL, output_refs TEXT NOT NULL, response_ref TEXT NOT NULL,
            tool_evidence_ref TEXT NOT NULL, created REAL NOT NULL)''')
        provenance.graph.db.execute('''CREATE TABLE IF NOT EXISTS execution_image_inputs(
            run TEXT NOT NULL,role TEXT NOT NULL,session TEXT NOT NULL,turn TEXT NOT NULL,image_refs TEXT NOT NULL,
            PRIMARY KEY(run,role,session,turn))''')
    def execute(self,*,run,role,claim,skills,prompt,model,input_versions,output_paths,session_id=None,review_subject=None,timeout=120,cancelled=lambda:False,instruction_length=None,image_refs=None):
        if timeout<=0 or not isinstance(input_versions,list) or any(not isinstance(x,str) for x in input_versions): raise ExecutorError('invalid execution inputs')
        image_paths=[]
        if image_refs is not None:
            from pathlib import Path
            from runner.local_supervisor import LocalSupervisor
            if role!='asset_review' or not review_subject or not isinstance(self.supervisor,LocalSupervisor) or not isinstance(image_refs,list) or not 1<=len(image_refs)<=20 or any(ref not in input_versions for ref in image_refs): raise ExecutorError('invalid review image inputs')
            root=Path(self.artifacts.directory)
            if not root.is_absolute() or root.resolve(strict=True)!=root: raise ExecutorError('private image store unavailable')
            total=0
            for ref in image_refs:
                data=self.artifacts.get(ref);total+=len(data)
                if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)>10485760 or total>20971520: raise ExecutorError('invalid or oversized review image')
                image_paths.append(str(root/ref))
        if self.model_policy is not None: self.model_policy.require(role,model)
        deadline=time.monotonic()+timeout
        if session_id is not None:
            capability=getattr(self.supervisor,'can_resume',None)
            contextual=callable(getattr(self.supervisor,'start_execution',None))
            if not callable(capability) or _bounded(lambda:capability(run,role,session_id,skills,model) if contextual else capability(run,role,session_id),deadline,cancelled) is not True: raise ExecutorError('persistent session unavailable')
        if _bounded(self.supervisor.preflight,deadline,cancelled) is not True: raise ExecutorError('isolated runtime preflight failed')
        execution=secrets.token_hex(32); db=self.provenance.graph.db
        db.execute('BEGIN IMMEDIATE')
        try:
            self.provenance.graph._lease(run,claim)
            db.execute('INSERT INTO execution_launch_audit(schema_version,run,role,execution,status,model,created) VALUES(1,?,?,?,?,?,?)',(run,role,execution,'requested',model,time.time()))
            db.execute('COMMIT')
        except Exception: db.execute('ROLLBACK'); raise
        handle=None; finished=False; starting=False; termination_unverified=False
        try:
            starting=True
            starter=getattr(self.supervisor,'start_execution',None)
            launch=(lambda:starter(run,role,session_id,skills,model)) if callable(starter) else (lambda:self.supervisor.start(run,role))
            handle=_bounded(launch,deadline,cancelled,late_cleanup=lambda late:late.cancel())
            if callable(getattr(handle,'open_transport',None)):
                transport=_bounded(lambda:handle.open_transport(deadline,cancelled),deadline,cancelled,late_cleanup=lambda _:handle.cancel())
            else:
                transport=ProcessTransport(handle.process,deadline,cancelled)
            def before_turn():
                if cancelled(): raise ExecutorError('execution cancelled')
                if callable(getattr(handle,'assert_active',None)): handle.assert_active()
            def on_turn(session,turn):
                db.execute('BEGIN IMMEDIATE')
                try:
                    self.provenance.graph._lease(run,claim)
                    if cancelled() or not self.provenance.verify(run,role,session,skills): raise ExecutorError('turn binding unavailable')
                    db.execute("INSERT INTO execution_link_audit(schema_version,run,role,execution,session,turn,status,created) VALUES(1,?,?,?,?,?,'observed',?)",(run,role,execution,session,turn,time.time()))
                    db.execute('COMMIT')
                except Exception: db.execute('ROLLBACK'); raise
            def on_tool(session,turn,item):
                evidence=self.artifacts.put(json.dumps({'run':run,'role':role,'session':session,'turn':turn,'items':[item]},sort_keys=True,separators=(',',':')).encode())
                state=item.get('status'); state='observed' if item['type']=='commandExecution' else state if state in ('completed','failed') else 'unavailable'
                db.execute('BEGIN IMMEDIATE')
                try:
                    self.provenance.graph._lease(run,claim)
                    if cancelled() or not self.provenance.verify(run,role,session,skills): raise ExecutorError('tool evidence binding unavailable')
                    db.execute('INSERT INTO tool_audit(schema_version,run,role,session,turn,tool_kind,status,input_present,output_present,evidence_ref,created) VALUES(1,?,?,?,?,?,?,?,?,?,?)',(run,role,session,turn,item['type'],state,int('arguments' in item or 'query' in item or 'command' in item),int('result' in item or 'aggregatedOutput' in item),evidence,time.time()))
                    db.execute("INSERT INTO execution_link_audit(schema_version,run,role,execution,session,turn,status,created) SELECT 1,?,?,?,?,?,'observed',? WHERE NOT EXISTS (SELECT 1 FROM execution_link_audit WHERE run=? AND execution=? AND session=? AND turn=? AND status='observed')",(run,role,execution,session,turn,time.time(),run,execution,session,turn))
                    db.execute('COMMIT')
                except Exception: db.execute('ROLLBACK'); raise
            local_output=getattr(handle,'output_path_prefix',None)
            if local_output is not None:
                prompt=_local_prompt(prompt,local_output,output_paths,instruction_length)
            result=run_codex(transport,self.provenance,run,role,claim,skills,prompt,model,session_id=session_id,review_subject=review_subject,record_completion=False,before_turn=before_turn,on_tool=on_tool,before_thread=getattr(handle,'verify_config',None),images=image_paths or None,on_thread=getattr(handle,'bind_session',None),on_turn=on_turn)
            if cancelled(): raise ExecutorError('execution cancelled')
            # The supervisor, not exit of a single process, must confirm the output tree is quiescent.
            if _bounded(handle.finish,deadline,cancelled) is not True: raise ExecutorError('worker termination uncertain')
            finished=True
            refs=collect_outputs(handle.output_dir,output_paths,self.artifacts)
            if image_refs is not None:
                for ref in image_refs: self.artifacts.get(ref)
            if cancelled(): raise ExecutorError('execution cancelled')
            response_ref=self.artifacts.put(result['output'].encode('utf-8'))
            tool_evidence_ref=self.artifacts.put(json.dumps({'run':run,'role':role,'session':result['session_id'],'turn':result['turn_id'],'items':result['tool_events']},sort_keys=True,separators=(',',':')).encode())
            db=self.provenance.graph.db; db.execute('BEGIN IMMEDIATE')
            try:
                self.provenance.graph._lease(run,claim)
                if not self.provenance.verify(run,role,result['session_id'],skills): raise ExecutorError('session provenance unavailable')
                from runner.local_supervisor import LocalSessionHandle
                if isinstance(handle,LocalSessionHandle):
                    from runner.local_scope import valid_scope_identity
                    unit=handle.handle.unit
                    identity=db.execute("SELECT invocation,cgroup FROM local_scopes WHERE run=? AND unit=? AND state='terminated'",(run,unit)).fetchone()
                    if (handle.run,handle.role)!=(run,role) or not identity or not valid_scope_identity(unit,*identity):
                        termination_unverified=True
                        raise ExecutorError('local scope termination evidence unavailable')
                    db.execute('INSERT INTO local_turn_scopes VALUES(?,?,?,?,?,?)',(run,role,result['session_id'],result['turn_id'],unit,handle.binding))
                if image_refs is not None:
                    db.execute('INSERT INTO execution_image_inputs VALUES(?,?,?,?,?)',(run,role,result['session_id'],result['turn_id'],json.dumps(image_refs)))
                db.execute('INSERT INTO completed_turn_outputs VALUES(?,?,?,?,?,?)',(run,role,result['session_id'],result['turn_id'],json.dumps(input_versions),json.dumps(refs)))
                db.execute('INSERT INTO execution_turn_audit(schema_version,run,role,session,turn,status,model,input_versions,output_refs,response_ref,tool_evidence_ref,created) VALUES(1,?,?,?,?,?,?,?,?,?,?,?)',(run,role,result['session_id'],result['turn_id'],'completed',model,json.dumps(input_versions),json.dumps(refs),response_ref,tool_evidence_ref,time.time()))
                db.execute('INSERT INTO execution_link_audit(schema_version,run,role,execution,session,turn,status,created) VALUES(1,?,?,?,?,?,?,?)',(run,role,execution,result['session_id'],result['turn_id'],'completed',time.time()))
                db.execute('INSERT INTO execution_launch_audit(schema_version,run,role,execution,status,model,created) VALUES(1,?,?,?,?,?,?)',(run,role,execution,'completed',model,time.time()))
                output_hash=hashlib.sha256(result['output'].encode()).hexdigest()
                db.execute('INSERT OR REPLACE INTO execution_completions VALUES(?,?,?,?)',(run,role,result['session_id'],output_hash))
                db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
            return {**result,'outputs':refs,'response_ref':response_ref,'tool_evidence_ref':tool_evidence_ref}
        except Exception:
            try:
                if handle is not None and not finished:
                    threading.Thread(target=handle.cancel,daemon=True).start()
                    self.fence(run)
                elif termination_unverified:
                    self.fence(run)
                elif handle is None and starting:
                    self.fence(run)
            finally:
                try: db.execute('INSERT INTO execution_launch_audit(schema_version,run,role,execution,status,model,created) VALUES(1,?,?,?,?,?,?)',(run,role,execution,'failed' if finished and not termination_unverified else 'uncertain',model,time.time()))
                except Exception: pass
            raise
    def review(self,*,subject,**kwargs):
        role=kwargs.get('role','')
        if not isinstance(subject,str) or not subject or kwargs.get('session_id') is not None or not isinstance(role,str) or not (role.endswith('_review') or role=='qa'): raise ExecutorError('fresh subject-bound review required')
        result=self.execute(review_subject=subject,session_id=None,**kwargs)
        review=parse_marketing_result(result['output'],role)
        if review['status'] not in ('passed','blocked') or subject not in review['input_versions'] or not review['outputs'] or any(ref not in result['outputs'] for ref in review['outputs']): raise ExecutorError('review output does not bind its subject and collected evidence')
        trusted_inputs=set(kwargs['input_versions']) | {subject}
        if any(ref not in trusted_inputs for ref in review['input_versions']) or any(item['evidence'] not in review['input_versions'] for item in review['findings']): raise ExecutorError('review finding evidence is not a reviewed input')
        if review['status']=='blocked' and not any(item['severity']=='blocking' and item['resolution']=='open' for item in review['findings']): raise ExecutorError('blocked review lacks actionable findings')
        result['review_status']=review['status']
        evidence={'run':kwargs['run'],'stage':role.removesuffix('_review')+'_draft' if role.endswith('_review') else 'implement', 'subject_hash':subject,
                  'reviewer_role':role,'reviewer_session':result['session_id'],'skills':kwargs['skills'],'review_output_ref':result['response_ref']}
        result['evidence_ref']=self.artifacts.put(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode())
        return result
