"""One explicit task and exact repository branch, using read-only clients."""
import re
import urllib.parse
from runner.task_intake import SingleTaskIntake
from workflows.engineering.github import GitHub

class ConfiguredTaskError(RuntimeError): pass

class ConfiguredTask:
    FIELDS={'run','workflow','project','number','ready_columns','completed_columns','blocker_direction','plan_endpoint','github_owner','github_repo','github_branch'}
    def __init__(self,service,config,plan,github):
        if not isinstance(config,dict) or not self.FIELDS <= set(config) or set(config)-self.FIELDS-{'task_format','github_base'}: raise ConfiguredTaskError('exact task configuration required')
        if not all(isinstance(config[k],str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',config[k]) for k in ('run','project','github_owner','github_repo')) or config['workflow'] not in ('marketing','engineering') or type(config['number']) is not int or config['number']<1: raise ConfiguredTaskError('invalid task reference')
        if not all(isinstance(config[k],list) and config[k] and all(type(x) is int for x in config[k]) for k in ('ready_columns','completed_columns')) or config['blocker_direction'] not in ('inward','outward'): raise ConfiguredTaskError('task readiness policy required')
        if config['workflow']=='marketing' and (not isinstance(config.get('task_format'),str) or not config['task_format'].strip()): raise ConfiguredTaskError('marketing task format required')
        GitHub._branch(config['github_branch'])
        if 'github_base' in config: GitHub._branch(config['github_base'])
        self.config=dict(config); self.github=github
        policy={'ready_columns':set(config['ready_columns']),'completed_columns':set(config['completed_columns']),'blocker_direction':config['blocker_direction'],'task_format':config.get('task_format')}
        self.intake=SingleTaskIntake(service,plan,self.discover,policy)
    def discover(self,project,number):
        c=self.config
        if (project,number)!=(c['project'],c['number']): raise ConfiguredTaskError('task reference mismatch')
        prefix='/repos/'+c['github_owner']+'/'+c['github_repo']; branch=c['github_branch']
        refs=self.github.request('GET',prefix+'/git/matching-refs/heads/'+urllib.parse.quote(branch,safe='/'))
        if not isinstance(refs,list) or len(refs)>=100 or any(not isinstance(x,dict) or not isinstance(x.get('ref'),str) for x in refs): raise ConfiguredTaskError('branch discovery incomplete')
        existing=[{'kind':'branch','ref':x['ref']} for x in refs if x['ref']=='refs/heads/'+branch]
        head=urllib.parse.quote(c['github_owner']+':'+branch,safe='')
        for page in range(1,101):
            items=self.github.request('GET',prefix+'/pulls?state=all&head='+head+'&per_page=100&page='+str(page))
            if not isinstance(items,list) or len(items)>100: raise ConfiguredTaskError('pull request discovery incomplete')
            for item in items:
                try:
                    if (type(item['number']) is not int or item['head']['ref']!=branch or item['head']['repo']['full_name']!=c['github_owner']+'/'+c['github_repo']): raise ValueError()
                    existing.append({'kind':'pull_request','number':item['number']})
                except (KeyError,TypeError,ValueError): raise ConfiguredTaskError('pull request discovery incomplete') from None
            if len(items)<100: return existing
        raise ConfiguredTaskError('pull request discovery incomplete')
    def submit(self,run,workflow,project,number):
        c=self.config
        if (run,workflow,project,number)!=(c['run'],c['workflow'],c['project'],c['number']): raise ConfiguredTaskError('configured task reference mismatch')
        return self.intake.submit(run,workflow,project,number)
    def revalidate(self,run):
        import json
        c=self.config
        if run!=c['run']: raise ConfiguredTaskError('task reference mismatch')
        current=self.intake.snapshot(c['workflow'],c['project'],c['number'])
        row=self.intake.service.graph.db.execute('SELECT artifact_ref FROM task_input_versions WHERE run=?',(run,)).fetchone()
        if not row: raise ConfiguredTaskError('admitted task unavailable')
        previous=json.loads(self.intake.service.artifacts.get(row[0]))
        if c['workflow']=='engineering' and current.get('existing_work')!=previous.get('existing_work'):
            baseline=previous.get('existing_work'); observed=current.get('existing_work')
            if not isinstance(baseline,list) or not isinstance(observed,list) or observed[:len(baseline)]!=baseline: return False
            extras=observed[len(baseline):]
            if not self._own_recorded_work(run,extras): return False
            current=dict(current); current['existing_work']=baseline
        from workflows.engineering.intake import readiness
        return not readiness(current,self.intake.clock().isoformat()) and current==previous

    def _own_recorded_work(self,run,extras):
        if not extras: return True
        try:
            import json
            from runner.engineering_dispatch import workspace_snapshot
            service=self.intake.service; c=self.config; current=workspace_snapshot(service,run)
            prefix='/repos/'+c['github_owner']+'/'+c['github_repo']; branch=c['github_branch']; head=current['head']; remote_head=None
            for item in extras:
                if item=={'kind':'branch','ref':'refs/heads/'+branch}:
                    remote=self.github.request('GET',prefix+'/git/ref/heads/'+urllib.parse.quote(branch,safe='/'))
                    remote_head=remote.get('object',{}).get('sha')
                    if not isinstance(remote_head,str): return False
                    records=service.graph.db.execute("SELECT b.manifest FROM action_bindings b JOIN external_actions a USING(run,action,version) WHERE b.run=? AND b.action LIKE 'push:%' AND b.version=? AND a.status IN ('executed','desired_state_observed')",(run,remote_head)).fetchall()
                    if not any((lambda target:target.get('repo')==current['repository'] and target.get('branch')==branch and target.get('head')==remote_head)(json.loads(row[0])) for row in records): return False
                    from runner.engineering_workspace import EngineeringWorkspace
                    root=service.model_supervisor.engineering_root if service.model_supervisor is not None else service.docker_supervisor.config.job_root.parent
                    registry=EngineeringWorkspace(root)
                    try: registry._git(current['path'],'merge-base','--is-ancestor',remote_head,head)
                    finally: registry.close()
                elif isinstance(item,dict) and set(item)=={'kind','number'} and item['kind']=='pull_request' and type(item['number']) is int:
                    records=service.graph.db.execute("SELECT result FROM pr_bridge_operations WHERE run=? AND kind='draft' AND state IN ('executed','desired_state_observed') AND result IS NOT NULL",(run,)).fetchall()
                    prior=[json.loads(service.artifacts.get(row[0])).get('result') for row in records]
                    if not any(isinstance(value,dict) and value.get('number')==item['number'] for value in prior): return False
                    remote=self.github.request('GET',prefix+'/pulls/'+str(item['number']))
                    if remote.get('number')!=item['number'] or remote.get('state')!='open' or remote_head is None or remote.get('head',{}).get('sha')!=remote_head or remote.get('head',{}).get('ref')!=branch or remote.get('head',{}).get('repo',{}).get('full_name')!=current['repository'] or remote.get('base',{}).get('ref')!=c.get('github_base') or remote.get('base',{}).get('repo',{}).get('full_name')!=current['repository']: return False
                else: return False
            return True
        except Exception: return False

    def submit_configured(self):
        c=self.config
        return self.submit(c['run'],c['workflow'],c['project'],c['number'])
