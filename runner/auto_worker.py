"""One configured task in a private worker thread, stopping at human gates.

No external operation is authorized by this loop. The service factory and all
runtime boundaries remain the same as the private control service.
"""
import secrets
import sqlite3
import threading
import time

class AutoWorkerError(RuntimeError): pass

def renew_leases(path,run,queue_claim,graph_claim,ttl=60):
    if ttl<=0: raise AutoWorkerError('invalid lease duration')
    db=sqlite3.connect(path,timeout=5,isolation_level=None)
    try:
        db.execute('BEGIN IMMEDIATE'); now=time.time()
        try:
            row=db.execute("SELECT j.status,j.token,j.expires,c.token,c.expires,r.status,a.state,w.state FROM jobs j JOIN graph_runs r ON r.id=j.run JOIN graph_claims c ON c.run=r.id JOIN manual_assignments a ON a.run=r.id JOIN manual_workers w ON w.target=a.target AND w.incarnation=a.incarnation WHERE j.run=?",(run,)).fetchone()
            if not row or row[0]!='running' or row[5]!='active' or row[6]!='running' or row[7]!='assigned' or not isinstance(queue_claim,str) or not isinstance(graph_claim,str) or not isinstance(row[1],str) or not isinstance(row[3],str) or not secrets.compare_digest(row[1],queue_claim) or not secrets.compare_digest(row[3],graph_claim) or row[2]<=now or row[4]<=now: raise AutoWorkerError('lease or manual fence unavailable')
            db.execute('UPDATE jobs SET expires=? WHERE run=? AND token=?',(now+ttl,run,queue_claim))
            db.execute('UPDATE graph_claims SET expires=? WHERE run=? AND token=?',(now+ttl,run,graph_claim))
            db.execute('COMMIT')
        except Exception: db.execute('ROLLBACK'); raise
    finally: db.close()

class AutoWorker:
    def __init__(self,factory,root):
        self.factory=factory; self.db_path=str(root/'state.db'); self.stop_event=threading.Event(); self.guard=threading.Lock(); self.thread=None; self.claimed_run=None; self.state={'status':'idle'}; self.pending_decision=None; self.pending_action=None; self.last_action=None; self.checkpoint_pending=False; self.at_gate=False; self.checkpoint_capable=False; self.wake=threading.Event()
    def status(self):
        with self.guard: return {**self.state,'last_action':self.last_action,'checkpoint_available':self.checkpoint_capable and self.at_gate and not self.stop_event.is_set() and not self.checkpoint_pending and self.pending_action is None and self.pending_decision is None}
    def _set(self,**value):
        with self.guard: self.state=value
    def start(self):
        with self.guard:
            if self.thread is not None: raise AutoWorkerError('automatic worker already started')
            self.state={'status':'starting'}; self.thread=threading.Thread(target=self._run,daemon=True); self.thread.start()
        return {'started':True}
    def request_decision(self,run,version,decision):
        if decision not in ('approved','rejected') or not isinstance(version,str) or len(version)!=64 or any(c not in '0123456789abcdef' for c in version): raise AutoWorkerError('invalid human decision')
        with self.guard:
            if self.state.get('run')!=run or self.state.get('status')!='awaiting_review' or self.state.get('version')!=version or self.pending_decision is not None or self.checkpoint_pending or self.stop_event.is_set(): raise AutoWorkerError('current review unavailable')
            self.pending_decision={'run':run,'version':version,'decision':decision}
        return {'pending':True,'version':version}
    def request_action(self,run,operation,parameters):
        allowed={'engineering_workspace_binding','bind_engineering_workspace','pr_propose','pr_approve','pr_execute','pr_review','pr_request_qa','pr_post_ready','pr_reconcile','pr_reauthorize'}
        if operation not in allowed or not isinstance(parameters,dict) or any(key in parameters for key in ('action','run','queue_claim','graph_claim','secret')): raise AutoWorkerError('invalid automatic action')
        with self.guard:
            if self.state.get('run')!=run or self.state.get('status') not in ('blocked','awaiting_approval','awaiting_review','awaiting_merge_review','no_runnable_stage') or self.pending_action is not None or self.checkpoint_pending or self.stop_event.is_set(): raise AutoWorkerError('automatic task unavailable or busy')
            ticket=secrets.token_hex(16); self.pending_action={'ticket':ticket,'operation':operation,'parameters':dict(parameters)}
        return {'pending':True,'ticket':ticket}
    def request_checkpoint(self,run):
        with self.guard:
            if not self.checkpoint_capable or not self.at_gate or self.state.get('run')!=run or self.checkpoint_pending or self.pending_action is not None or self.pending_decision is not None or self.stop_event.is_set(): raise AutoWorkerError('checkpoint unavailable')
            self.checkpoint_pending=True; self.wake.set()
        return {'pending':True}
    def stop(self,service=None):
        with self.guard:
            self.stop_event.set(); self.wake.set()
            run=self.claimed_run
            if service is not None and run is not None:
                row=service.graph.db.execute("SELECT j.status,a.state FROM jobs j JOIN manual_assignments a ON a.run=j.run WHERE j.run=? AND a.target=? AND a.incarnation=?",(run,service.lifecycle.target['manual_target'],service.lifecycle.incarnation)).fetchone()
                if row and row[0] in ('running','cancelling') and row[1] in ('running','pending'):
                    service.fence_execution(run)
    def _run(self):
        service=None; queue=None; run=None; heartbeat_stop=threading.Event(); failed=threading.Event(); heartbeat=None
        try:
            service,private_control=self.factory(); intake=service.task_intake
            from runner.manual_lifecycle import ManualLifecycle
            with self.guard: self.checkpoint_capable=isinstance(service.lifecycle,ManualLifecycle) and service.model_supervisor is not None
            if intake is None or service.lifecycle is None or not isinstance(getattr(intake,'config',None),dict): raise AutoWorkerError('configured task and manual lifecycle required')
            run=intake.config['run']; existing=service.graph.db.execute('SELECT 1 FROM graph_runs WHERE id=?',(run,)).fetchone()
            if not existing: intake.submit_configured()
            candidate=service.graph.db.execute("SELECT run FROM jobs WHERE status='queued' ORDER BY seq LIMIT 1").fetchone()
            if candidate!=(run,): raise AutoWorkerError('configured task is not the next queued task')
            with self.guard:
                if self.stop_event.is_set(): return
                queue=service.lifecycle.claim()
                if not queue: raise AutoWorkerError('configured task not claimable')
                self.claimed_run=queue['run']
                if queue['run']!=run:
                    run=queue['run']
                    raise AutoWorkerError('claimed task differs from configuration')
                claim=service.claim(run)
            renew_leases(self.db_path,run,queue['token'],claim)
            def keepalive():
                while not heartbeat_stop.wait(10):
                    try: renew_leases(self.db_path,run,queue['token'],claim)
                    except Exception: failed.set(); break
            heartbeat=threading.Thread(target=keepalive,daemon=True); heartbeat.start()
            while not self.stop_event.is_set() and not failed.is_set():
                with self.guard:
                    if self.checkpoint_pending:
                        from runner.checkpoint import checkpoint,CheckpointError
                        try: checkpoint(service,run,queue['token'],claim)
                        except CheckpointError:
                            self.checkpoint_pending=False; self.at_gate=False
                            self.state={**self.state,'reason':'checkpoint unavailable; work remains active or unresolved'}
                        else:
                            self.claimed_run=None;self.at_gate=False;self.checkpoint_pending=False
                            self.state={'status':'checkpointed','run':run};return
                    self.at_gate=False
                    pending_action=self.pending_action; self.pending_action=None
                if pending_action is not None:
                    try:
                        if private_control is None: raise AutoWorkerError('private control unavailable')
                        request={**pending_action['parameters'],'action':pending_action['operation'],'run':run,'queue_claim':queue['token'],'graph_claim':claim}
                        action_result=private_control.handle(private_control.secret,request)
                        outcome={'ticket':pending_action['ticket'],'status':'completed','result':action_result}
                    except Exception: outcome={'ticket':pending_action['ticket'],'status':'inspection_required'}
                    with self.guard: self.last_action=outcome
                result=service.stage_dispatcher.step(run,queue['token'],claim)
                state=result.get('status','unknown'); stage=result.get('stage'); version=result.get('version')
                if state=='awaiting_review':
                    assembly=service.graph.get(run,'assembly')
                    if assembly and assembly['status']=='awaiting_review': version=assembly['version']
                self._set(status=state,stage=stage,run=run,version=version,reason=result.get('reason'))
                if state=='uncertain': raise AutoWorkerError('stage outcome uncertain; inspection required')
                if state=='awaiting_review' and service.human_review is not None:
                    with self.guard:
                        pending=self.pending_decision; self.pending_decision=None
                    if pending is not None:
                        try: service.human_review.decide(run,queue['token'],claim,pending['version'],pending['decision'])
                        except Exception: self._set(status=state,stage=stage,run=run,version=version,reason='human decision rejected; review package changed or evidence unavailable')
                    decision=service.human_review.current(run)
                    if decision and decision['decision']=='approved':
                        with self.guard:
                            if self.stop_event.is_set(): raise AutoWorkerError('automatic worker stopped; inspection required')
                            service.finish_job(run,queue['token'])
                        self._set(status='completed',run=run); return
                    if decision and decision['decision']=='rejected':
                        self._set(status='revision_required',stage=stage,run=run,version=version,reason='human rejected the review package')
                        while not self.stop_event.wait(5) and not failed.is_set(): pass
                        break
                if state in ('blocked','awaiting_approval','awaiting_review','awaiting_merge_review','rate_limited','no_runnable_stage'):
                    with self.guard: self.at_gate=True
                    self.wake.wait(30);self.wake.clear()
                    if self.stop_event.is_set(): break
            if failed.is_set(): raise AutoWorkerError('lease renewal failed; inspection required')
            if self.stop_event.is_set(): raise AutoWorkerError('automatic worker stopped; inspection required')
        except Exception:
            if service is not None and queue is not None:
                with self.guard:
                    try: service.fence_execution(run)
                    except Exception: pass
            self._set(status='inspection_required',run=run,reason='automatic worker stopped or encountered an unresolved outcome')
        finally:
            with self.guard: self.at_gate=False;self.checkpoint_capable=False
            heartbeat_stop.set()
            if heartbeat is not None: heartbeat.join(timeout=2)
            if service is not None:
                if service.lifecycle is not None and callable(getattr(service.lifecycle,'close',None)): service.lifecycle.close()
                service.close()
