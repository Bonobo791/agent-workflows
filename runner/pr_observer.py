"""Observe desired remote state after the exact disposable process has exited.

A positive observation records current state, not causal attribution or proof
that GitHub has finished processing a disconnected request. Negative outcomes
remain uncertain.
"""
import json
import sqlite3
import urllib.parse
from workflows.engineering.review_loop import BoundLedger

class PRRemoteObserver:
    def __init__(self,service,http): self.service,self.http=service,http
    @staticmethod
    def _matches_pr(value,target,number):
        try:
            return value['number']==number and value['state']=='open' and value['head']['sha']==target['head'] and value['head']['ref']==target['branch'] and value['head']['repo']['full_name']==target['repo'] and value['base']['ref']==target['base'] and value['base']['repo']['full_name']==target['repo']
        except (KeyError,TypeError): return False
    def observe(self,run,action,target,scope):
        try:
            manifest=BoundLedger.encode(target); db=sqlite3.connect(self.service.state_db_path)
            try:
                ledger=db.execute('SELECT b.manifest,a.status FROM action_bindings b JOIN external_actions a USING(run,action,version) WHERE b.run=? AND b.action=? AND b.version=?',(run,action,target['head'])).fetchone()
                rows=db.execute("SELECT p.assignment,p.kind,a.container_id,a.state,d.policy_hash,m.role FROM pr_mutation_processes p JOIN docker_execution_map m ON m.assignment=p.assignment AND m.task_run=p.run JOIN docker_assignments a ON a.run=p.assignment JOIN docker_disposable_assignments d ON d.run=a.run AND d.container_id=a.container_id JOIN docker_assignment_policy pol ON pol.run=a.run AND pol.policy_hash=d.policy_hash WHERE p.run=? AND p.action=? AND p.scope=?",(run,action,scope)).fetchall()
                proposals=db.execute('SELECT proposal FROM pr_bridge_operations WHERE run=? AND state IN (\'uncertain\',\'observed\')',(run,)).fetchall()
            finally: db.close()
            if not ledger or ledger[0]!=manifest or ledger[1] not in ('uncertain','executed') or len(rows)!=1: raise ValueError('process unavailable')
            assignment,kind,cid,_,policy,role=rows[0]
            if role!=('push' if kind=='push' else 'pr_mutation'): raise ValueError('process role mismatch')
            if policy!=self.service.docker_supervisor.config.policy_hash: raise ValueError('policy mismatch')
            status,data=self.service.docker_supervisor.assignments.engine.request('GET','/containers/'+cid+'/json')
            if status!=200 or data['Id']!=cid or data['Config']['Labels'].get('agent-workflows.run')!=assignment or data['State']['Running'] is not False or data['State']['Status']!='exited' or type(data['State'].get('ExitCode')) is not int or data['HostConfig']['RestartPolicy']['Name']!='no' or data['HostConfig']['AutoRemove'] is not False: raise ValueError('process still running or identity mismatch')
            matches=[]
            for (ref,) in proposals:
                item=json.loads(self.service.artifacts.get(ref))
                if item.get('action')==action and item.get('target')==target: matches.append(item)
            if len(matches)!=1: raise ValueError('proposal unavailable')
            proposal=matches[0]; args=proposal['args']; prefix='/repos/'+target['repo']
            if kind=='push' and proposal['kind']=='push':
                if args['expected_remote'] is None:
                    refs=self.http.request('GET',prefix+'/git/matching-refs/heads/'+urllib.parse.quote(target['branch'],safe='/'))
                    if not isinstance(refs,list) or len(refs)>=100: raise ValueError('branch listing incomplete')
                    exact=[item for item in refs if item.get('ref')=='refs/heads/'+target['branch']]
                    if len(exact)>1: raise ValueError('ambiguous branch')
                    actual=exact[0].get('object',{}).get('sha') if exact else None
                else:
                    remote=self.http.request('GET',prefix+'/git/ref/heads/'+urllib.parse.quote(target['branch'],safe='/'))
                    actual=remote.get('object',{}).get('sha')
                if actual!=target['head']: raise ValueError('desired ref not observed')
                return {'quiescent':True,'outcome':'desired_state_observed'}
            if kind=='draft' and proposal['kind']=='draft':
                owner=target['repo'].split('/')[0]; head=urllib.parse.quote(owner+':'+target['branch'],safe='')
                items=self.http.request('GET',prefix+'/pulls?state=all&head='+head+'&per_page=100&page=1')
                if not isinstance(items,list) or len(items)>=100 or len(items)>1: raise ValueError('PR listing incomplete or ambiguous')
                if not items: raise ValueError('draft outcome remains uncertain')
                number=items[0].get('number')
                if type(number) is not int or number<1: raise ValueError('PR identity unavailable')
                receipt=self.http.request('GET',prefix+'/pulls/'+str(number))
                if not self._matches_pr(receipt,target,number) or receipt.get('draft') is not True or receipt.get('title')!=args['title'] or receipt.get('body')!=args['body']: raise ValueError('draft receipt mismatch')
                return {'quiescent':True,'outcome':'desired_state_observed','remote_receipt':receipt}
            if kind=='ready' and proposal['kind']=='ready':
                number=args['number']; receipt=self.http.request('GET',prefix+'/pulls/'+str(number))
                if not self._matches_pr(receipt,target,number) or receipt.get('node_id')!=args['node'] or type(receipt.get('draft')) is not bool: raise ValueError('PR identity mismatch')
                if receipt['draft']: raise ValueError('readiness outcome remains uncertain')
                return {'quiescent':True,'outcome':'desired_state_observed','remote_receipt':receipt}
            raise ValueError('no independently fenced process for this action type')
        except Exception: return {'quiescent':False,'outcome':'unknown'}
