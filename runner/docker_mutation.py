"""One approved PR mutation in a dedicated, disposable non-model container."""
import hashlib
import json
import secrets
import time
from runner.docker_attach import DockerAttachTransport
from runner.docker_controller import DockerAssignments, DockerController, DockerError
from runner.engineering_dispatch import workspace_snapshot
from workflows.engineering.github import GitHub

class DockerMutationTransport:
    def __init__(self,service,run,queue_claim,graph_claim,credentials):
        self.service,self.run,self.queue_claim,self.graph_claim,self.credentials=service,run,queue_claim,graph_claim,credentials
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS pr_mutation_processes(assignment TEXT PRIMARY KEY,run TEXT NOT NULL,action TEXT NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL)')
    def execute(self,kind,repo,payload):
        s=self.service; supervisor=s.docker_supervisor; cfg=supervisor.config; db=s.graph.db
        if cfg is None or cfg.network=='none' or not callable(getattr(self.credentials,'ready',None)) or self.credentials.ready() is not True or not callable(getattr(self.credentials,'issue',None)): raise DockerError('protected mutation credential channel unavailable')
        if kind not in ('draft','ready') or not s.queue.may_start(self.run,self.queue_claim) or supervisor.fence_ready(self.run) is not True: raise DockerError('active task required')
        workspace=workspace_snapshot(s,self.run); assignment='job-'+secrets.token_hex(24)
        db.execute('BEGIN IMMEDIATE')
        try:
            s.graph._lease(self.run,self.graph_claim)
            if not s.queue.may_start(self.run,self.queue_claim): raise DockerError('task claim unavailable')
            proposals=db.execute("SELECT proposal FROM pr_bridge_operations WHERE run=? AND kind=? AND state='uncertain'",(self.run,kind)).fetchall()
            selected=[]
            for (ref,) in proposals:
                p=json.loads(s.artifacts.get(ref)); t=p['target']; a=p['args']
                if t['repo']!=repo or t['repo']!=workspace['repository'] or t['head']!=workspace['head'] or t['branch']!=workspace['branch'] or t['diff']!=hashlib.sha256(workspace['diff'].encode()).hexdigest(): continue
                expected={'head':t['branch'],'base':t['base'],'title':a['title'],'body':a['body'],'draft':True} if kind=='draft' else {'node':a['node']}
                if payload!=expected: continue
                scope='github:'+repo+(':branch:'+t['branch'] if kind=='draft' else ':pr:'+str(a['number']))
                owner,name=repo.split('/'); github=GitHub(None,owner,name)
                action=github.draft_action(t['branch'],t['base'],a['title'],a['body']) if kind=='draft' else github.ready_action(a['number'],a['node'],a['digest'])
                if action!=p['action']: continue
                approved=db.execute("SELECT b.manifest FROM external_actions x JOIN action_scopes z ON z.run=x.run AND z.action=x.action AND z.version=x.version JOIN action_bindings b ON b.run=x.run AND b.action=x.action AND b.version=x.version WHERE x.run=? AND x.action=? AND x.version=? AND x.status='uncertain' AND z.scope=?",(self.run,action,t['head'],scope)).fetchone()
                if approved and json.loads(approved[0])==t: selected.append((action,scope))
            if len(selected)!=1: raise DockerError('exact mutation approval unavailable')
            action,scope=selected[0]
            if db.execute("SELECT 1 FROM docker_execution_map m LEFT JOIN docker_assignments a ON a.run=m.assignment WHERE m.task_run=? AND (a.run IS NULL OR a.state!='terminated')",(self.run,)).fetchone(): raise DockerError('prior execution unresolved')
            db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',(self.run,'pr_mutation',assignment))
            db.execute('INSERT INTO pr_mutation_processes VALUES(?,?,?,?,?)',(assignment,self.run,action,scope,kind)); db.execute('COMMIT')
        except Exception: db.execute('ROLLBACK'); raise
        mounts=cfg.create_job_dirs(assignment); assignments=DockerAssignments(supervisor.db_path,supervisor.assignments.engine,cfg.image); sock=None
        try:
            cid=assignments.create_disposable(assignment,cfg,mounts,mutation={'task':self.run,'action':action,'scope':scope,'kind':kind})
            sock=assignments.attach(assignment)
            if supervisor.fence_ready(self.run) is not True or not s.queue.may_start(self.run,self.queue_claim): raise DockerError('task cancelled before mutation')
            assignments.start(assignment,allow_completed=True)
            _,state=DockerController(supervisor.assignments.engine,{assignment:cid})._inspect(assignment)
            if state['Status']!='running' or state['Running'] is not True or supervisor.fence_ready(self.run) is not True or not s.queue.may_start(self.run,self.queue_claim): raise DockerError('mutation assignment unavailable')
            issued=self.credentials.issue(self.run,action,assignment,cid)
            if not isinstance(issued,dict) or set(issued)!={'run','action','assignment','container_id','expires','credential'} or (issued['run'],issued['action'],issued['assignment'],issued['container_id'])!=(self.run,action,assignment,cid) or type(issued['expires']) not in (int,float) or not time.time()<issued['expires']<=time.time()+300: raise DockerError('scoped credential binding unavailable')
            token=issued.pop('credential')
            if not isinstance(token,str) or not token or len(token)>8192 or any(c in token for c in '\r\n\0'): raise DockerError('scoped credential unavailable')
            stream=DockerAttachTransport(sock,time.monotonic()+30,lambda:not s.queue.may_start(self.run,self.queue_claim) or supervisor.fence_ready(self.run) is not True)
            stream.send({'credential':token,'kind':kind,'repo':repo,'payload':payload}); token=None
            reply=stream.receive()
            if not isinstance(reply,dict) or set(reply)!={'ok','result'} or reply['ok'] is not True or not isinstance(reply['result'],dict): raise DockerError('mutation result uncertain')
            status,result=supervisor.assignments.engine.request('POST','/containers/'+cid+'/wait?condition=not-running')
            if status!=200 or not isinstance(result,dict) or result.get('StatusCode')!=0 or result.get('Error'): raise DockerError('mutation termination uncertain')
            _,state=DockerController(supervisor.assignments.engine,{assignment:cid})._inspect(assignment)
            if state['Running'] is not False or state['Status']!='exited' or state.get('ExitCode')!=0: raise DockerError('mutation termination unverified')
            db.execute('BEGIN IMMEDIATE')
            try:
                if db.execute("UPDATE docker_assignments SET state='terminated' WHERE run=? AND container_id=? AND state='running'",(assignment,cid)).rowcount!=1: raise DockerError('assignment changed')
                db.execute('INSERT INTO docker_terminal_evidence VALUES(?,?,?,?)',(assignment,cid,'exited',time.time())); db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
            output=reply['result']
            if kind=='draft':
                if set(output)!={'number','draft','head'} or type(output['number']) is not int or output['number']<1 or output['draft'] is not True or output['head']!={'sha':workspace['head']}: raise DockerError('invalid draft result')
            elif output!={'id':payload['node'],'isDraft':False}: raise DockerError('invalid readiness result')
            return output
        except Exception: raise DockerError('mutation outcome uncertain; inspection required') from None
        finally:
            if sock is not None: sock.close()
            assignments.close()
