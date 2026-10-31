"""Exact approved draft/readiness mutation through the local GitHub CLI."""
import hashlib
import json
import secrets
import tempfile
import time
from pathlib import Path
from runner.engineering_dispatch import workspace_snapshot
from workflows.engineering.github import GitHub

class LocalMutationError(RuntimeError): pass

class LocalMutationTransport:
    def __init__(self,service,run,queue_claim,graph_claim,gh):
        self.service,self.run,self.queue_claim,self.graph_claim,self.gh=service,run,queue_claim,graph_claim,gh
        db=service.graph.db
        db.execute('CREATE TABLE IF NOT EXISTS local_mutation_attempts(assignment TEXT PRIMARY KEY,run TEXT NOT NULL,action TEXT NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS local_pr_scopes(unit TEXT PRIMARY KEY,assignment TEXT NOT NULL,run TEXT NOT NULL,action TEXT NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL)')
    def execute(self,kind,repo,payload):
        from runner.local_command import _run_scoped
        service=self.service;s=service.model_supervisor;db=service.graph.db
        if kind not in ('draft','ready') or s is None or s.preflight() is not True or s.fence(self.run) is not True or not service.queue.may_start(self.run,self.queue_claim) or self.gh._digest()!=self.gh.digest: raise LocalMutationError('local mutation boundary unavailable')
        service.graph._lease(self.run,self.graph_claim);workspace=workspace_snapshot(service,self.run);assignment='job-'+secrets.token_hex(24)
        db.execute('BEGIN IMMEDIATE')
        try:
            service.graph._lease(self.run,self.graph_claim)
            if not service.queue.may_start(self.run,self.queue_claim) or s.fence(self.run) is not True:raise LocalMutationError('task claim unavailable')
            proposals=db.execute("SELECT proposal FROM pr_bridge_operations WHERE run=? AND kind=? AND state='uncertain'",(self.run,kind)).fetchall();selected=[]
            for (ref,) in proposals:
                p=json.loads(service.artifacts.get(ref));target=p['target'];args=p['args']
                if target['repo']!=repo or target['repo']!=workspace['repository'] or target['head']!=workspace['head'] or target['branch']!=workspace['branch'] or target['diff']!=hashlib.sha256(workspace['diff'].encode()).hexdigest():continue
                expected={'head':target['branch'],'base':target['base'],'title':args['title'],'body':args['body'],'draft':True} if kind=='draft' else {'node':args['node']}
                if payload!=expected:continue
                scope='github:'+repo+(':branch:'+target['branch'] if kind=='draft' else ':pr:'+str(args['number']))
                owner,name=repo.split('/');github=GitHub(None,owner,name)
                action=github.draft_action(target['branch'],target['base'],args['title'],args['body']) if kind=='draft' else github.ready_action(args['number'],args['node'],args['digest'])
                if action!=p['action']:continue
                approval=db.execute("SELECT b.manifest FROM external_actions x JOIN action_scopes z ON z.run=x.run AND z.action=x.action AND z.version=x.version JOIN action_bindings b ON b.run=x.run AND b.action=x.action AND b.version=x.version WHERE x.run=? AND x.action=? AND x.version=? AND x.status='uncertain' AND z.scope=?",(self.run,action,target['head'],scope)).fetchone()
                if approval and json.loads(approval[0])==target:selected.append((action,scope,target))
            if len(selected)!=1:raise LocalMutationError('exact mutation approval unavailable')
            action,scope,target=selected[0]
            if db.execute("SELECT 1 FROM local_scopes WHERE run=? AND state!='terminated'",(self.run,)).fetchone():raise LocalMutationError('prior scope unresolved')
            db.execute("INSERT INTO local_mutation_attempts VALUES(?,?,?,?,?,'reserved')",(assignment,self.run,action,scope,kind));db.execute('COMMIT')
        except Exception:db.execute('ROLLBACK');raise
        if kind=='draft':endpoint='/repos/'+repo+'/pulls';body=payload
        else:
            endpoint='graphql';body={'query':'mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { pullRequest { id isDraft } } }','variables':{'id':payload['node']}}
        encoded=json.dumps(body,separators=(',',':'),ensure_ascii=False).encode('utf-8')
        if len(encoded)>32768:raise LocalMutationError('mutation input exceeds limit')
        env=dict(self.gh.environment)
        command=[str(self.gh.executable),'api','--hostname','github.com','--method','POST','--input','-','--',endpoint]
        def reserve(unit):db.execute('INSERT INTO local_pr_scopes VALUES(?,?,?,?,?,?)',(unit,assignment,self.run,action,scope,kind))
        def verify(unit):
            service.graph._lease(self.run,self.graph_claim)
            live=db.execute("SELECT 1 FROM external_actions a JOIN action_scopes z ON z.run=a.run AND z.action=a.action AND z.version=a.version WHERE a.run=? AND a.action=? AND a.version=? AND a.status='uncertain' AND z.scope=?",(self.run,action,target['head'],scope)).fetchone()
            if not live or self.gh._digest()!=self.gh.digest or workspace_snapshot(service,self.run)!=workspace or s.fence(self.run) is not True or not service.queue.may_start(self.run,self.queue_claim):raise LocalMutationError('mutation authorization changed')
        try:
            with tempfile.TemporaryDirectory(prefix='pr-mutation-',dir=s.engineering_root) as work:
                result=_run_scoped(s,self.run,work,command,env,time.monotonic()+60,1048576,reserve,verify,input_data=encoded)
                if result['exit_code']!=0:raise ValueError()
                value=json.loads(result['stdout'])
                if kind=='draft':
                    output={'number':value['number'],'draft':value['draft'],'head':{'sha':value['head']['sha']}}
                    if type(output['number']) is not int or output['number']<1 or output['draft'] is not True or output['head']['sha']!=workspace['head']:raise ValueError()
                else:
                    if value.get('errors'):raise ValueError()
                    output=value['data']['markPullRequestReadyForReview']['pullRequest']
                    if output!={'id':payload['node'],'isDraft':False}:raise ValueError()
                terminal=db.execute("SELECT 1 FROM local_pr_scopes p JOIN local_scopes x ON x.unit=p.unit AND x.run=p.run WHERE p.assignment=? AND p.kind=? AND p.unit=? AND x.state='terminated'",(assignment,kind,result['unit'])).fetchone()
                if not terminal or db.execute("UPDATE local_mutation_attempts SET state='terminated' WHERE assignment=? AND state='reserved'",(assignment,)).rowcount!=1:raise ValueError()
                return output
        except Exception:raise LocalMutationError('mutation outcome requires inspection') from None
