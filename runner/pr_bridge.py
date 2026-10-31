"""Authenticated control bridge for exact, individually approved GitHub actions.

No authority is installed by default. The authority must supply an isolated
push-capable transport and independent uncertain-outcome observer.
"""
import hashlib
import json
import secrets
import time
from runner.engineering_dispatch import workspace_snapshot, approval_package
from workflows.engineering.github import GitHubError
from workflows.engineering.review_loop import BoundLedger, ReviewLoop

class PRBridgeError(RuntimeError): pass

class PRBridge:
    def __init__(self,service):
        self.service=service
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS pr_bridge_operations(proposal TEXT PRIMARY KEY,run TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,result TEXT)')
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS pr_reauthorization_history(id INTEGER PRIMARY KEY AUTOINCREMENT,proposal TEXT NOT NULL,reason TEXT NOT NULL,actor TEXT NOT NULL,created REAL NOT NULL)')
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS pr_review_blockers(run TEXT NOT NULL,number INTEGER NOT NULL,comment_id TEXT NOT NULL,head TEXT NOT NULL,state TEXT NOT NULL,evidence_ref TEXT,PRIMARY KEY(run,number,comment_id,head))')
        if 'evidence_ref' not in [row[1] for row in service.graph.db.execute('PRAGMA table_info(pr_review_blockers)')]: service.graph.db.execute('ALTER TABLE pr_review_blockers ADD COLUMN evidence_ref TEXT')
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS review_audit(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, run TEXT NOT NULL, number INTEGER NOT NULL, comment_id TEXT NOT NULL, head TEXT NOT NULL, status TEXT NOT NULL, evidence_ref TEXT, created REAL NOT NULL)')
        service.graph.db.execute('''CREATE TRIGGER IF NOT EXISTS review_audit_insert AFTER INSERT ON pr_review_blockers BEGIN
            INSERT INTO review_audit(schema_version,run,number,comment_id,head,status,evidence_ref,created) VALUES(1,NEW.run,NEW.number,NEW.comment_id,NEW.head,NEW.state,NEW.evidence_ref,(julianday('now')-2440587.5)*86400.0);
        END''')
        service.graph.db.execute('''CREATE TRIGGER IF NOT EXISTS review_audit_update AFTER UPDATE OF state ON pr_review_blockers WHEN NEW.state!=OLD.state BEGIN
            INSERT INTO review_audit(schema_version,run,number,comment_id,head,status,evidence_ref,created) VALUES(1,NEW.run,NEW.number,NEW.comment_id,NEW.head,NEW.state,NEW.evidence_ref,(julianday('now')-2440587.5)*86400.0);
        END''')
    def _loop(self,run,queue_claim,graph_claim,*,reconcile=False):
        service=self.service; authority=service.pr_authority; intake=service.task_intake
        if authority is not None and callable(getattr(authority,'for_claim',None)): authority=authority.for_claim(run,queue_claim,graph_claim)
        if authority is None or not callable(getattr(authority,'ready',None)) or authority.ready() is not True or not callable(getattr(authority,'observe',None)) or intake is None: raise PRBridgeError('isolated PR authority and observer unavailable')
        c=intake.config
        if c.get('run')!=run or c.get('workflow')!='engineering' or not c.get('github_base'): raise PRBridgeError('exact repository and base branch required')
        def locked(selected):
            supervisor=service.model_supervisor or service.docker_supervisor
            fence=getattr(supervisor,'fence',None) if service.model_supervisor is not None else getattr(supervisor,'fence_ready',None)
            if selected!=run or not service.queue.may_start(run,queue_claim) or not callable(fence) or fence(run) is not True: return False
            try: service.graph._lease(run,graph_claim); return True
            except Exception: return False
        def workspace(selected):
            if not locked(selected): raise PRBridgeError('task claim unavailable')
            current=workspace_snapshot(service,selected)
            task=service.graph.db.execute('SELECT task_id FROM graph_runs WHERE id=?',(selected,)).fetchone()
            if not task: raise PRBridgeError('task unavailable')
            return {'task':task[0],'repo':current['repository'],'branch':current['branch'],'base':c['github_base'],'head':current['head'],'diff':hashlib.sha256(current['diff'].encode()).hexdigest()}
        if not locked(run): raise PRBridgeError('task claim unavailable')
        if not reconcile and (not callable(getattr(intake,'revalidate',None)) or intake.revalidate(run) is not True): raise PRBridgeError('task freshness unavailable')
        ledger=BoundLedger(service.state_db_path,workspace,locked)
        try:
            loop=ReviewLoop(authority.http,c['github_owner'],c['github_repo'],ledger,locked,workspace); loop.authority=authority; return loop
        except Exception: ledger.db.close(); raise
    @staticmethod
    def _assert_pr(loop,target,number,node=None):
        value=loop.github.http.request('GET',loop.github.prefix+'/pulls/'+str(number))
        try:
            if value['state']!='open' or value['number']!=number or value['head']['ref']!=target['branch'] or value['head']['repo']['full_name']!=target['repo'] or value['base']['ref']!=target['base'] or value['base']['repo']['full_name']!=target['repo'] or value['head']['sha']!=target['head'] or (node is not None and value['node_id']!=node): raise ValueError('PR target mismatch')
        except (KeyError,TypeError): raise PRBridgeError('PR target unavailable') from None
        return value
    @staticmethod
    def _action(loop,target,kind,args):
        if kind=='push' and isinstance(args,dict) and set(args)=={'expected_remote'}: return loop.github.push_action(target['branch'],args['expected_remote'])
        if kind=='draft' and isinstance(args,dict) and set(args)=={'title','body'}: return loop.github.draft_action(target['branch'],target['base'],args['title'],args['body'])
        if kind=='ready' and isinstance(args,dict) and set(args)=={'number','node','digest','dispositions'}:
            PRBridge._assert_pr(loop,target,args['number'],args['node'])
            _,digest=loop.review(target['run'],args['number'],args['dispositions'])
            if digest!=args['digest']: raise PRBridgeError('review changed')
            return loop.github.ready_action(args['number'],args['node'],digest)
        raise PRBridgeError('unsupported exact action')
    def _record_stage(self,run,claim,stage,payload,status='passed'):
        graph=self.service.graph
        ref=self.service.artifacts.put(json.dumps(payload,sort_keys=True,separators=(',',':')).encode())
        snapshot=graph.prepare(run,stage,claim)
        graph.finish(run,stage,snapshot,status,ref,claim)
        return ref

    def _ensure_draft_stage(self,loop,run,claim,number,target):
        if self.service.graph.get(run,'open_draft'): return
        rows=self.service.graph.db.execute("SELECT proposal,result FROM pr_bridge_operations WHERE run=? AND kind='draft' AND state IN ('executed','desired_state_observed') AND result IS NOT NULL",(run,)).fetchall()
        for proposal_ref,result_ref in rows:
            try:
                proposal=json.loads(self.service.artifacts.get(proposal_ref)); result=json.loads(self.service.artifacts.get(result_ref))['result']
                if result['number']!=number or proposal['target']['repo']!=target['repo'] or proposal['target']['branch']!=target['branch'] or proposal['target']['base']!=target['base']: continue
                remote=self._assert_pr(loop,target,number)
                if remote['draft'] is not True: continue
                self._record_stage(run,claim,'open_draft',{'proposal':proposal_ref,'result_ref':result_ref,'number':number,'head':target['head']})
                return
            except Exception: continue
        raise PRBridgeError('verified draft record unavailable')

    def propose(self,run,queue_claim,graph_claim,kind,args):
        loop=self._loop(run,queue_claim,graph_claim)
        try:
            target=loop.target(run); package=approval_package(self.service,run)
            if callable(getattr(loop.authority,'supports',None)) and loop.authority.supports(kind) is not True: raise PRBridgeError('isolated action provider unavailable')
            if kind=='ready' and self.service.graph.db.execute("SELECT 1 FROM pr_review_blockers WHERE run=? AND number=? AND state='open'",(run,args.get('number'))).fetchone(): raise PRBridgeError('valid review findings require correction')
            lookup={**target,'run':run}; action=self._action(loop,lookup,kind,args)
            proposal={'run':run,'kind':kind,'args':args,'target':target,'action':action,'package':package}
            ref=self.service.artifacts.put(json.dumps(proposal,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
            self.service.graph.db.execute("INSERT OR IGNORE INTO pr_bridge_operations VALUES(?,?,?,'proposed',NULL)",(ref,run,kind))
            return {'proposal':ref,'action':action,'target':target,'package':package}
        finally: loop.ledger.db.close()
    def _load(self,loop,run,ref):
        try:
            proposal=json.loads(self.service.artifacts.get(ref))
            row=self.service.graph.db.execute('SELECT run,kind,state FROM pr_bridge_operations WHERE proposal=?',(ref,)).fetchone()
            if not row or row[:2]!=(run,proposal['kind']) or proposal['run']!=run or loop.target(run)!=proposal['target'] or approval_package(self.service,run)!=proposal['package'] or self._action(loop,{**proposal['target'],'run':run},proposal['kind'],proposal['args'])!=proposal['action']: raise ValueError('stale proposal')
            if proposal['kind']=='ready' and self.service.graph.db.execute("SELECT 1 FROM pr_review_blockers WHERE run=? AND number=? AND state='open'",(run,proposal['args']['number'])).fetchone(): raise ValueError('valid review findings require correction')
            return proposal,row[2]
        except Exception: raise PRBridgeError('proposal unavailable or stale') from None
    def approve(self,run,queue_claim,graph_claim,ref,expires):
        if type(expires) not in (int,float) or not time.time()<expires<=time.time()+3600: raise PRBridgeError('approval expiry invalid')
        loop=self._loop(run,queue_claim,graph_claim)
        try:
            proposal,state=self._load(loop,run,ref)
            if state!='proposed': raise PRBridgeError('proposal not available')
            def record(db):
                claims=db.execute("SELECT j.token,j.expires,c.token,c.expires FROM jobs j JOIN graph_runs r ON r.id=j.run AND r.status='active' JOIN graph_claims c ON c.run=r.id AND c.task_id=r.task_id WHERE j.run=? AND j.status='running'",(run,)).fetchone()
                if not claims or not all(isinstance(value,str) for value in (queue_claim,graph_claim,claims[0],claims[2])) or not secrets.compare_digest(queue_claim,claims[0]) or not secrets.compare_digest(graph_claim,claims[2]) or claims[1] is None or claims[3] is None or claims[1]<=time.time() or claims[3]<=self.service.graph.clock(): raise PRBridgeError('current claims unavailable')
                if db.execute("UPDATE pr_bridge_operations SET state='approved' WHERE proposal=? AND run=? AND state='proposed'",(ref,run)).rowcount!=1: raise PRBridgeError('proposal changed')
            loop.ledger.approve_bound(run,proposal['action'],proposal['target'],'local-owner',expires,record=record)
            return {'approved':True,'proposal':ref,'action':proposal['action']}
        finally: loop.ledger.db.close()
    def withdraw(self,run,queue_claim,graph_claim,ref):
        try:
            proposal=json.loads(self.service.artifacts.get(ref))
            if not isinstance(proposal,dict) or proposal.get('run')!=run or not isinstance(proposal.get('action'),str) or not isinstance(proposal.get('target'),dict) or not isinstance(proposal['target'].get('head'),str): raise ValueError('invalid proposal')
            db=self.service.graph.db;db.execute('BEGIN IMMEDIATE')
            try:
                self.service.graph._lease(run,graph_claim)
                job=db.execute('SELECT status,token,expires FROM jobs WHERE run=?',(run,)).fetchone()
                if not job or job[0]!='running' or not isinstance(queue_claim,str) or not isinstance(job[1],str) or not secrets.compare_digest(queue_claim,job[1]) or job[2] is None or job[2]<=time.time(): raise PRBridgeError('queue claim unavailable')
                operation=db.execute('SELECT run,kind,state FROM pr_bridge_operations WHERE proposal=?',(ref,)).fetchone()
                if not operation or operation[:2]!=(run,proposal.get('kind')) or operation[2] not in ('approved','proposed') or db.execute('SELECT 1 FROM action_scopes WHERE run=?',(run,)).fetchone(): raise PRBridgeError('unused approval unavailable')
                if db.execute('SELECT manifest FROM action_bindings WHERE run=? AND action=? AND version=?',(run,proposal['action'],proposal['target']['head'])).fetchone()!=(BoundLedger.encode(proposal['target']),): raise PRBridgeError('approval binding unavailable')
                if operation[2]=='proposed' and db.execute("SELECT 1 FROM pr_bridge_operations WHERE run=? AND proposal<>? AND (state IS NULL OR state NOT IN ('proposed','not_executed','executed','desired_state_observed','reconciled'))",(run,ref)).fetchone(): raise PRBridgeError('other operation unresolved')
                prior=db.execute('SELECT COALESCE(MAX(id),0) FROM action_audit').fetchone()[0]
                if db.execute("UPDATE external_actions SET status='revoked',actor='local-owner',updated=? WHERE run=? AND action=? AND version=? AND status='approved'",(time.time(),run,proposal['action'],proposal['target']['head'])).rowcount!=1: raise PRBridgeError('approval unavailable')
                if not db.execute("SELECT 1 FROM action_audit WHERE id>? AND run=? AND action=? AND version=? AND status='revoked' AND actor='local-owner'",(prior,run,proposal['action'],proposal['target']['head'])).fetchone(): raise PRBridgeError('approval audit unavailable')
                if operation[2]=='approved' and db.execute("UPDATE pr_bridge_operations SET state='proposed' WHERE proposal=? AND state='approved'",(ref,)).rowcount!=1: raise PRBridgeError('proposal changed')
                reason='owner_withdrawn' if operation[2]=='approved' else 'orphaned_approval_withdrawn'
                if db.execute("INSERT INTO pr_reauthorization_history(proposal,reason,actor,created) VALUES(?,?,'local-owner',?)",(ref,reason,time.time())).rowcount!=1: raise PRBridgeError('withdrawal history unavailable')
                db.execute('COMMIT');return {'revoked':True,'proposal':ref}
            except Exception: db.execute('ROLLBACK');raise
        except Exception: raise PRBridgeError('approval withdrawal unavailable') from None

    def reauthorize(self,run,queue_claim,graph_claim,ref):
        """Permit a new explicit approval only when an old attempt did not execute."""
        loop=self._loop(run,queue_claim,graph_claim)
        try:
            proposal,state=self._load(loop,run,ref)
            if state not in ('not_executed','approved'): raise PRBridgeError('reauthorization unavailable')
            db=self.service.graph.db; db.execute('BEGIN IMMEDIATE')
            try:
                self.service.graph._lease(run,graph_claim)
                row=db.execute('SELECT a.status,b.expires FROM external_actions a JOIN action_bindings b USING(run,action,version) WHERE a.run=? AND a.action=? AND a.version=?',(run,proposal['action'],proposal['target']['head'])).fetchone()
                if not row or db.execute('SELECT 1 FROM action_scopes WHERE run=?',(run,)).fetchone(): raise PRBridgeError('action outcome unresolved')
                if state=='approved':
                    if row[0]!='approved' or row[1]>time.time(): raise PRBridgeError('approval has not expired')
                    if db.execute("UPDATE external_actions SET status='not_executed',updated=? WHERE run=? AND action=? AND version=? AND status='approved'",(time.time(),run,proposal['action'],proposal['target']['head'])).rowcount!=1: raise PRBridgeError('approval changed')
                    reason='expired'
                elif row[0]=='not_executed': reason='confirmed_not_executed'
                else: raise PRBridgeError('action outcome unresolved')
                if db.execute("UPDATE pr_bridge_operations SET state='proposed' WHERE proposal=? AND state=?",(ref,state)).rowcount!=1: raise PRBridgeError('proposal changed')
                db.execute('INSERT INTO pr_reauthorization_history(proposal,reason,actor,created) VALUES(?,?,?,?)',(ref,reason,'local-owner',time.time()))
                db.execute('COMMIT'); return {'reauthorization_available':True,'proposal':ref,'reason':reason}
            except Exception: db.execute('ROLLBACK'); raise
        finally: loop.ledger.db.close()

    def execute(self,run,queue_claim,graph_claim,ref):
        loop=self._loop(run,queue_claim,graph_claim)
        try:
            proposal,state=self._load(loop,run,ref)
            if state!='approved': raise PRBridgeError('exact approval required')
            if self.service.graph.db.execute("UPDATE pr_bridge_operations SET state='uncertain' WHERE proposal=? AND state='approved'",(ref,)).rowcount!=1: raise PRBridgeError('action unavailable')
            args=proposal['args']; kind=proposal['kind']
            try:
                result=loop.push(run,args['expected_remote']) if kind=='push' else loop.draft(run,args['title'],args['body']) if kind=='draft' else loop.ready(run,args['number'],args['node'],args['digest'],args['dispositions'])
                output=self.service.artifacts.put(json.dumps({'result':result},sort_keys=True,separators=(',',':')).encode())
                self.service.graph.db.execute("UPDATE pr_bridge_operations SET state='observed',result=? WHERE proposal=? AND state='uncertain'",(output,ref))
                if kind=='draft': self._record_stage(run,graph_claim,'open_draft',{'proposal':ref,'result_ref':output,'number':result['number'],'head':proposal['target']['head']})
                if kind=='ready': self._record_stage(run,graph_claim,'ready',{'proposal':ref,'result_ref':output,'number':args['number'],'head':proposal['target']['head']})
                self.service.graph.db.execute("UPDATE pr_bridge_operations SET state='executed' WHERE proposal=? AND state='observed'",(ref,))
                return {'executed':True,'result_ref':output}
            except Exception:
                status=loop.ledger.db.execute('SELECT status FROM external_actions WHERE run=? AND action=? AND version=?',(run,proposal['action'],proposal['target']['head'])).fetchone()
                if status==('not_executed',): self.service.graph.db.execute("UPDATE pr_bridge_operations SET state='not_executed' WHERE proposal=? AND state='uncertain'",(ref,))
                raise PRBridgeError('action outcome requires independent inspection') from None
        finally: loop.ledger.db.close()
    def _corrected_by_review(self,run,head,old_head,review_ref):
        from runner.structured import parse_marketing_result
        service=self.service
        try:
            current=service.graph.get(run,'implement')
            if not current or current['status']!='passed': return False
            writer=json.loads(service.artifacts.get(current['version']))
            if writer['commit']!=head: return False
            rows=service.graph.db.execute("SELECT writer_version,review_version FROM correction_attempts WHERE run=? AND writer_stage='implement' AND state='completed' AND result_version=?",(run,current['version'])).fetchall()
            for writer_version,review_version in rows:
                original=json.loads(service.artifacts.get(writer_version)); qa=json.loads(service.artifacts.get(review_version))
                if original['commit']!=old_head or qa['commit']!=old_head or review_ref not in qa['input_versions'] or review_version not in writer['input_versions']: continue
                proof=json.loads(service.artifacts.get(qa['_review_evidence'])); reviewer=qa['session_id']; subject=original['evidence_ref']
                resolved=service.skill_resolver.resolve(service.required_skills['qa']); skills={key:value['sha256'] for key,value in resolved.items()}
                if reviewer==original['session_id'] or proof.get('run')!=run or proof.get('stage')!='implement' or proof.get('reviewer_role')!='qa' or proof.get('reviewer_session')!=reviewer or proof.get('subject_hash')!=subject or proof.get('skills')!=skills or not service.provenance.verify(run,'qa',reviewer,skills) or not service.provenance.completed(run,'qa',reviewer) or not service.provenance.subject_matches(run,'qa',reviewer,subject) or proof.get('review_output_ref')!=service.provenance.completion_hash(run,'qa',reviewer): continue
                parsed=parse_marketing_result(service.artifacts.get(proof['review_output_ref']).decode(),'qa')
                public={k:v for k,v in qa.items() if not k.startswith('_') and k not in ('session_id','commit')}
                if parsed==public and parsed['status']=='blocked' and any(item['severity']=='blocking' and item['resolution']=='open' and item['evidence']==subject for item in parsed['findings']): return True
            return False
        except Exception: return False

    def review(self,run,queue_claim,graph_claim,number,dispositions):
        loop=self._loop(run,queue_claim,graph_claim)
        try:
            target=loop.target(run); self._assert_pr(loop,target,number); self._ensure_draft_stage(loop,run,graph_claim,number,target); snapshot=loop.github.review_snapshot(number,target['head'])
            try: digest=loop.github.triage_digest(snapshot,dispositions); status='ready_for_approval'
            except GitHubError: digest=None; status='blocked_for_correction'
            ref=self.service.artifacts.put(json.dumps({'run':run,'number':number,'snapshot':snapshot,'dispositions':dispositions,'digest':digest,'status':status},sort_keys=True,separators=(',',':')).encode())
            db=self.service.graph.db; db.execute('BEGIN IMMEDIATE')
            try:
                self.service.graph._lease(run,graph_claim)
                for comment_id,item in dispositions.items():
                    if isinstance(item,dict) and item.get('disposition')=='valid' and any(comment.get('id')==comment_id for comment in snapshot['comments']):
                        db.execute("INSERT OR IGNORE INTO pr_review_blockers(run,number,comment_id,head,state,evidence_ref) VALUES(?,?,?,?,'open',?)",(run,number,comment_id,target['head'],ref))
                if status=='ready_for_approval':
                    approval_package(self.service,run)
                    blockers=db.execute("SELECT comment_id,head,evidence_ref FROM pr_review_blockers WHERE run=? AND number=? AND state='open' AND head!=?",(run,number,target['head'])).fetchall()
                    for comment_id,old_head,evidence_ref in blockers:
                        if evidence_ref and self._corrected_by_review(run,target['head'],old_head,evidence_ref):
                            db.execute("UPDATE pr_review_blockers SET state='resolved' WHERE run=? AND number=? AND comment_id=? AND head=? AND state='open'",(run,number,comment_id,old_head))
                if db.execute("SELECT 1 FROM pr_review_blockers WHERE run=? AND number=? AND state='open'",(run,number)).fetchone():
                    status='blocked_for_correction'; digest=None
                    final_ref=self.service.artifacts.put(json.dumps({'run':run,'number':number,'snapshot':snapshot,'dispositions':dispositions,'digest':digest,'status':status},sort_keys=True,separators=(',',':')).encode())
                    if final_ref!=ref:
                        db.execute('UPDATE pr_review_blockers SET evidence_ref=? WHERE run=? AND number=? AND evidence_ref=?',(final_ref,run,number,ref))
                        ref=final_ref
                db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
            self._record_stage(run,graph_claim,'triage',{'review_ref':ref,'number':number,'head':target['head'],'digest':digest},'passed' if status=='ready_for_approval' else 'blocked')
            return {'status':status,'evidence_ref':ref,'digest':digest}
        finally: loop.ledger.db.close()
    def request_qa(self,run,queue_claim,graph_claim,evidence_ref):
        from runner.engineering_dispatch import dispatch
        loop=self._loop(run,queue_claim,graph_claim)
        try:
            evidence=json.loads(self.service.artifacts.get(evidence_ref)); target=loop.target(run)
            if evidence.get('run')!=run or evidence.get('status')!='blocked_for_correction' or evidence.get('snapshot',{}).get('head')!=target['head'] or not any(isinstance(item,dict) and item.get('disposition')=='valid' for item in evidence.get('dispositions',{}).values()): raise PRBridgeError('valid review evidence unavailable')
            self._assert_pr(loop,target,evidence['number'])
            if not self.service.graph.db.execute("SELECT 1 FROM pr_review_blockers WHERE run=? AND number=? AND head=? AND state='open' AND evidence_ref=?",(run,evidence['number'],target['head'],evidence_ref)).fetchone(): raise PRBridgeError('recorded valid finding unavailable')
            if loop.github.review_snapshot(evidence['number'],target['head'])!=evidence['snapshot']: raise PRBridgeError('review snapshot changed')
            return dispatch(self.service.stage_dispatcher,run,'qa',queue_claim,graph_claim,feedback_ref=evidence_ref)
        finally: loop.ledger.db.close()

    def post_ready(self,run,queue_claim,graph_claim,number,node,dispositions):
        loop=self._loop(run,queue_claim,graph_claim)
        try:
            target=loop.target(run); self._assert_pr(loop,target,number,node)
            digest=loop.post_ready(run,number,node,dispositions)
            self._record_stage(run,graph_claim,'post_ready',{'number':number,'node':node,'head':target['head'],'digest':digest,'dispositions':dispositions})
            return {'digest':digest,'status':'human_merge_review'}
        finally: loop.ledger.db.close()
    def verify_final(self,run,queue_claim,graph_claim,record):
        loop=self._loop(run,queue_claim,graph_claim)
        try:
            target=loop.target(run); pr=self._assert_pr(loop,target,record['number'],record['node'])
            if pr.get('draft') is not False or target['head']!=record['head']: raise PRBridgeError('final review target changed')
            _,digest=loop.review(run,record['number'],record['dispositions'])
            if digest!=record['digest']: raise PRBridgeError('post-ready review changed')
            return True
        finally: loop.ledger.db.close()

    def reconcile(self,run,queue_claim,graph_claim,ref):
        loop=self._loop(run,queue_claim,graph_claim,reconcile=True)
        try:
            proposal=json.loads(self.service.artifacts.get(ref)); row=self.service.graph.db.execute('SELECT run,kind,state FROM pr_bridge_operations WHERE proposal=?',(ref,)).fetchone()
            if not row or row[0]!=run or row[1]!=proposal['kind'] or row[2] not in ('uncertain','observed') or proposal['run']!=run: raise PRBridgeError('uncertain action unavailable')
            args=proposal['args']; target=proposal['target']; kind=proposal['kind']
            scope='github:'+target['repo']+(':branch:'+target['branch'] if kind=='push' else ':pr:'+str(args['number']) if kind=='ready' else ':branch:'+target['branch'])
            if row[2]=='observed':
                ledger_row=loop.ledger.db.execute('SELECT b.manifest,a.status FROM action_bindings b JOIN external_actions a USING(run,action,version) WHERE b.run=? AND b.action=? AND b.version=?',(run,proposal['action'],target['head'])).fetchone()
                observed=loop.authority.observe(run,proposal['action'],target,scope)
                if ledger_row!=(loop.ledger.encode(target),'executed') or not isinstance(observed,dict) or observed.get('quiescent') is not True or observed.get('outcome') not in ('executed','desired_state_observed') or loop.target(run)!=target: raise PRBridgeError('confirmed action unavailable')
                stored=self.service.graph.db.execute("SELECT result FROM pr_bridge_operations WHERE proposal=? AND state='observed'",(ref,)).fetchone()
                if not stored or not stored[0]: raise PRBridgeError('action receipt unavailable')
                result=json.loads(self.service.artifacts.get(stored[0]))['result']
                if kind=='draft':
                    self._assert_pr(loop,target,result['number'])
                    self._record_stage(run,graph_claim,'open_draft',{'proposal':ref,'result_ref':stored[0],'number':result['number'],'head':target['head']})
                elif kind=='ready':
                    self._assert_pr(loop,target,args['number'],args['node'])
                    self._record_stage(run,graph_claim,'ready',{'proposal':ref,'result_ref':stored[0],'number':args['number'],'head':target['head']})
                self.service.graph.db.execute("UPDATE pr_bridge_operations SET state='executed' WHERE proposal=? AND state='observed'",(ref,))
                return {'reconciled':True,'inspection_required':False}
            if kind in ('draft','ready'):
                observed=loop.authority.observe(run,proposal['action'],target,scope)
                if not isinstance(observed,dict) or observed.get('quiescent') is not True or observed.get('outcome') not in ('executed','not_executed','desired_state_observed'): raise PRBridgeError('remote outcome remains uncertain')
                if observed['outcome']=='not_executed' and set(observed)=={'quiescent','outcome'}:
                    loop.reconcile(run,proposal['action'],target,scope,lambda *_:observed)
                    self.service.graph.db.execute("UPDATE pr_bridge_operations SET state='not_executed' WHERE proposal=? AND state='uncertain'",(ref,))
                    return {'reconciled':True,'inspection_required':False,'executed':False}
                if observed['outcome'] not in ('executed','desired_state_observed') or set(observed)!={'quiescent','outcome','remote_receipt'} or not isinstance(observed['remote_receipt'],dict): raise PRBridgeError('remote receipt unavailable')
                receipt=observed['remote_receipt']; number=receipt.get('number')
                if type(number) is not int or number<1 or (kind=='ready' and number!=args['number']): raise PRBridgeError('verified PR identity unavailable')
                current=self._assert_pr(loop,target,number,args['node'] if kind=='ready' else None)
                if current!=receipt or (kind=='draft' and (current.get('draft') is not True or current.get('title')!=args['title'] or current.get('body')!=args['body'])) or (kind=='ready' and current.get('draft') is not False): raise PRBridgeError('PR receipt changed')
                result={'result':current} if kind=='draft' else {'result':{'pull_request':current,'post_ready_review_required':True}}
                result_ref=self.service.artifacts.put(json.dumps(result,sort_keys=True,separators=(',',':')).encode())
                stage='open_draft' if kind=='draft' else 'ready'
                stage_ref=self.service.artifacts.put(json.dumps({'proposal':ref,'result_ref':result_ref,'number':number,'head':target['head']},sort_keys=True,separators=(',',':')).encode())
                snapshot=self.service.graph.prepare(run,stage,graph_claim)
                self.service.graph.finish(run,stage,snapshot,'passed',stage_ref,graph_claim,pr_reconcile={'proposal':ref,'action':proposal['action'],'head':target['head'],'scope':scope,'result_ref':result_ref,'kind':kind})
                return {'reconciled':True,'inspection_required':False,'outcome':'desired_state_observed','result_ref':result_ref}
            observed=loop.authority.observe(run,proposal['action'],target,scope)
            if not isinstance(observed,dict) or set(observed)!={'quiescent','outcome'} or observed['quiescent'] is not True or observed['outcome'] not in ('executed','not_executed','desired_state_observed'): raise PRBridgeError('remote outcome remains uncertain')
            loop.reconcile(run,proposal['action'],target,scope,lambda *_:observed)
            state='not_executed' if observed['outcome']=='not_executed' else 'desired_state_observed' if observed['outcome']=='desired_state_observed' else 'reconciled'
            self.service.graph.db.execute("UPDATE pr_bridge_operations SET state=? WHERE proposal=? AND state='uncertain'",(state,ref))
            return {'reconciled':True,'inspection_required':state=='reconciled','outcome':observed['outcome']}
        finally: loop.ledger.db.close()
