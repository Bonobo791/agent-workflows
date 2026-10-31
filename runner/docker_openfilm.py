"""Exact, network-disabled Docker rendering for an approved OpenFilm project."""
import hashlib
import io
import json
import os
import secrets
import sqlite3
import time
import zipfile
from contextlib import closing
from pathlib import Path
from runner.docker_controller import DockerAssignments, DockerController, DockerError
from runner.openfilm_stage import _verify_project
from workflows.marketing.adapters import MarketingAdapter, AdapterError


class DockerOpenFilmBackend:
    """Project author and host-policy verification are separate trusted boundaries."""
    def __init__(self, supervisor, artifacts, builder, verify_policy, timeout_seconds=600):
        if supervisor.config is None or not callable(verify_policy) or type(timeout_seconds) is not int or not 1<=timeout_seconds<=600: raise DockerError('render configuration unavailable')
        if not all(callable(getattr(builder,name,None)) for name in ('ready','supports','build','verify','cancel')): raise DockerError('project builder unavailable')
        self.supervisor,self.artifacts,self.builder,self.verify_policy=supervisor,artifacts,builder,verify_policy
        self.timeout_seconds=timeout_seconds
        self.policy_hash=self._policy()
        db=sqlite3.connect(supervisor.db_path)
        try:
            db.execute('''CREATE TABLE IF NOT EXISTS openfilm_render_jobs(
                assignment TEXT PRIMARY KEY, task_run TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE,
                policy_hash TEXT NOT NULL, state TEXT NOT NULL, brief_ref TEXT NOT NULL,
                project_ref TEXT, proof_ref TEXT, terminal_ref TEXT, result_ref TEXT)''')
            db.commit()
        finally: db.close()
    def _policy(self):
        helper=Path(__file__).with_name('openfilm_render.mjs').read_bytes()
        data={'docker_policy':self.supervisor.config.policy_hash,'openfilm':'0.2.6','helper_sha256':hashlib.sha256(helper).hexdigest(),'timeout_seconds':self.timeout_seconds,'network':'none'}
        verifier=getattr(self.verify_policy,'policy_hash',None)
        if isinstance(verifier,str): data['verifier_policy']=verifier
        return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def ready(self):
        try:
            if self._policy()!=self.policy_hash or self.supervisor.fence_ready(None) is not True or self.builder.ready() is not True or self.verify_policy(self.policy_hash) is not True: return False
            with closing(sqlite3.connect(self.supervisor.db_path)) as db:
                return all(db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action=? AND version=? AND status='approved'",(action,version)).fetchone() for action,version in (('docker_config',self.supervisor.config.policy_hash),('openfilm_policy',self.policy_hash)))
        except Exception: return False
    def blocker(self,brief):
        if self.builder.supports(brief) is True: return None
        return 'supplied media unavailable or unsupported' if any(item.get('media') for item in brief.get('assets',[])) else 'project builder does not support this brief'
    def _active(self,db,run,attempt_id,graph_claim,queue_claim):
        row=db.execute("SELECT version,count FROM graph_attempts WHERE run=? AND stage='asset_production' AND status='uncertain'",(run,)).fetchone()
        if not row: return False
        actual=hashlib.sha256(json.dumps([run,'asset_production',row[0],row[1]],separators=(',',':')).encode()).hexdigest()
        graph=db.execute('SELECT token,expires FROM graph_claims WHERE run=?',(run,)).fetchone()
        job=db.execute('SELECT status,token,expires FROM jobs WHERE run=?',(run,)).fetchone()
        return bool(actual==attempt_id and graph and isinstance(graph_claim,str) and isinstance(graph[0],str) and secrets.compare_digest(graph[0],graph_claim) and graph[1]>time.time() and job and job[0]=='running' and isinstance(queue_claim,str) and isinstance(job[1],str) and secrets.compare_digest(job[1],queue_claim) and job[2]>time.time() and self.supervisor.fence_ready(run) is True)
    def execute(self,*,run,attempt_id,brief_ref,brief,canonical,graph_claim,queue_claim):
        if not self.ready() or json.loads(self.artifacts.get(brief_ref))!=brief or hashlib.sha256(canonical.encode('utf-8')).hexdigest()!=brief.get('content_ref'): raise DockerError('render inputs unavailable')
        assignment='of-'+attempt_id
        db=sqlite3.connect(self.supervisor.db_path,timeout=10,isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not self._active(db,run,attempt_id,graph_claim,queue_claim): raise DockerError('render attempt unavailable')
                db.execute('INSERT INTO openfilm_render_jobs(assignment,task_run,attempt_id,policy_hash,state,brief_ref) VALUES(?,?,?,?,?,?)',(assignment,run,attempt_id,self.policy_hash,'building',brief_ref))
                db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
            built=self.builder.build(run=run,attempt_id=attempt_id,brief_ref=brief_ref,brief=brief,canonical=canonical,graph_claim=graph_claim,queue_claim=queue_claim)
            if not isinstance(built,dict) or set(built)!={'project_ref','proof_ref'} or self.builder.verify(run,attempt_id,brief_ref,built['project_ref'],built['proof_ref']) is not True: raise DockerError('project provenance unavailable')
            self.artifacts.get(built['proof_ref']); _verify_project(self.artifacts,built['project_ref'],len(brief['assets']))
            mounts=self.supervisor.config.create_job_dirs(assignment)
            project=Path(mounts['workspace'])/'project'; project.mkdir(mode=0o700)
            with zipfile.ZipFile(io.BytesIO(self.artifacts.get(built['project_ref']))) as archive: archive.extractall(project)
            (Path(mounts['workspace'])/'brief.json').write_text(json.dumps(brief,ensure_ascii=False),encoding='utf-8')
            db.execute('BEGIN IMMEDIATE')
            try:
                if not self._active(db,run,attempt_id,graph_claim,queue_claim) or db.execute("SELECT state FROM openfilm_render_jobs WHERE assignment=?",(assignment,)).fetchone()!=('building',): raise DockerError('render cancelled or stale')
                if db.execute("SELECT 1 FROM docker_execution_map m LEFT JOIN docker_assignments a ON a.run=m.assignment WHERE m.task_run=? AND (a.run IS NULL OR a.state!='terminated')",(run,)).fetchone(): raise DockerError('prior execution unresolved')
                db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',(run,'openfilm_render',assignment))
                db.execute("UPDATE openfilm_render_jobs SET state='launching',project_ref=?,proof_ref=? WHERE assignment=?",(built['project_ref'],built['proof_ref'],assignment))
                db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
            assignments=DockerAssignments(self.supervisor.db_path,self.supervisor.assignments.engine,self.supervisor.config.image)
            try:
                cid=assignments.create_disposable(assignment,self.supervisor.config,mounts,openfilm={'task':run,'attempt_id':attempt_id,'policy_hash':self.policy_hash})
                if db.execute('SELECT state FROM openfilm_render_jobs WHERE assignment=?',(assignment,)).fetchone()!=('launching',) or not self._active(db,run,attempt_id,graph_claim,queue_claim): raise DockerError('render cancelled before start')
                assignments.start(assignment,allow_completed=True)
                if db.execute('SELECT state FROM openfilm_render_jobs WHERE assignment=?',(assignment,)).fetchone()!=('launching',) or not self._active(db,run,attempt_id,graph_claim,queue_claim):
                    try: self.supervisor.finish(assignment)
                    except Exception: pass
                    raise DockerError('render cancelled after start')
                status,wait=self.supervisor.assignments.engine.request('POST','/containers/'+cid+'/wait?condition=not-running',timeout=self.timeout_seconds)
                _,state=DockerController(self.supervisor.assignments.engine,{assignment:cid})._inspect(assignment)
                if status!=200 or not isinstance(wait,dict) or type(wait.get('StatusCode')) is not int or wait.get('Error') or state['Running'] or state['Status']!='exited' or state.get('ExitCode')!=wait['StatusCode']: raise DockerError('render termination uncertain')
                outputs=[]
                if state['ExitCode']==0:
                    result_ref=MarketingAdapter(self.artifacts,None).ingest(mounts['output'],'render-result.json',max_bytes=1048576)
                    payload=json.loads(self.artifacts.get(result_ref))
                    if not isinstance(payload,dict) or set(payload)!={'outputs'} or not isinstance(payload['outputs'],list): raise DockerError('render result unavailable')
                    outputs=payload['outputs']
                else:
                    try:
                        failure_ref=MarketingAdapter(self.artifacts,None).ingest(mounts['output'],'look-failure.json',max_bytes=1048576)
                        outputs=[{'look_report_ref':failure_ref}]
                    except AdapterError: pass
                terminal={'run':run,'assignment':assignment,'attempt_id':attempt_id,'brief_ref':brief_ref,'project_ref':built['project_ref'],'proof_ref':built['proof_ref'],'policy_hash':self.policy_hash,'container_id':cid,'exit_code':state['ExitCode']}
                terminal_ref=self.artifacts.put(json.dumps(terminal,sort_keys=True,separators=(',',':')).encode())
                result={'attempt_id':attempt_id,'status':'exited' if state['ExitCode']==0 else 'failed','root':mounts['output'],'project_ref':built['project_ref'],'outputs':outputs,'terminal_ref':terminal_ref}
                ref=self.artifacts.put(json.dumps(result,sort_keys=True,separators=(',',':')).encode())
                db.execute('BEGIN IMMEDIATE')
                try:
                    if not self._active(db,run,attempt_id,graph_claim,queue_claim) or db.execute("UPDATE openfilm_render_jobs SET state='exited',terminal_ref=?,result_ref=? WHERE assignment=? AND state='launching'",(terminal_ref,ref,assignment)).rowcount!=1: raise DockerError('render outcome changed')
                    if db.execute("UPDATE docker_assignments SET state='terminated' WHERE run=? AND container_id=? AND state='running'",(assignment,cid)).rowcount!=1: raise DockerError('render assignment changed')
                    db.execute('INSERT INTO docker_terminal_evidence VALUES(?,?,?,?)',(assignment,cid,'exited',time.time()))
                    db.execute('COMMIT')
                except Exception: db.execute('ROLLBACK'); raise
                return result
            finally: assignments.close()
        finally: db.close()
    def cancel(self,run,attempt_id):
        assignment='of-'+attempt_id
        with closing(sqlite3.connect(self.supervisor.db_path,timeout=10,isolation_level=None)) as db:
            db.execute("UPDATE openfilm_render_jobs SET state='cancel_requested' WHERE assignment=? AND task_run=? AND state IN ('building','launching')",(assignment,run))
            row=db.execute('SELECT state FROM docker_assignments WHERE run=?',(assignment,)).fetchone()
        try: self.builder.cancel(run,attempt_id)
        finally:
            if row and row[0]=='running': self.supervisor.finish(assignment)
    def verify_terminal(self,run,attempt_id,brief_ref,result):
        try:
            if self._policy()!=self.policy_hash: return False
            assignment='of-'+attempt_id
            with closing(sqlite3.connect(self.supervisor.db_path)) as db:
                row=db.execute("SELECT project_ref,proof_ref,terminal_ref,result_ref FROM openfilm_render_jobs WHERE assignment=? AND task_run=? AND attempt_id=? AND policy_hash=? AND brief_ref=? AND state='exited'",(assignment,run,attempt_id,self.policy_hash,brief_ref)).fetchone()
                container=db.execute("SELECT a.container_id FROM docker_assignments a JOIN docker_terminal_evidence e ON e.run=a.run AND e.container_id=a.container_id WHERE a.run=? AND a.state='terminated' AND e.status='exited'",(assignment,)).fetchone()
            if not row or not container or json.loads(self.artifacts.get(row[3]))!=result or row[2]!=result['terminal_ref'] or row[0]!=result['project_ref'] or self.builder.verify(run,attempt_id,brief_ref,row[0],row[1]) is not True: return False
            self.artifacts.get(row[0]); self.artifacts.get(row[1])
            terminal=json.loads(self.artifacts.get(row[2])); cid=container[0]
            if terminal!={'run':run,'assignment':assignment,'attempt_id':attempt_id,'brief_ref':brief_ref,'project_ref':row[0],'proof_ref':row[1],'policy_hash':self.policy_hash,'container_id':cid,'exit_code':terminal.get('exit_code')}: return False
            status,details=self.supervisor.assignments.engine.request('GET','/containers/'+cid+'/json')
            state=details['State']; host=details['HostConfig']; config=details['Config']
            expected={(str(self.supervisor.config.job_root/assignment/'workspace'),'/workspace'),(str(self.supervisor.config.job_root/assignment/'output'),'/output')}
            if self.supervisor.config.engine=='podman-rootless':
                mounts=details['Mounts']
                actual={(item['Source'],item['Destination']) for item in mounts if item['Type']=='bind' and item['RW'] is True}
                label=details['MountLabel']
                process_label=details['ProcessLabel']
                podman_ok=(details['Image']==self.supervisor.config.image and host.get('Annotations',{}).get('io.podman.annotations.userns')=='keep-id' and isinstance(label,str) and label.startswith('system_u:object_r:container_file_t:') and isinstance(process_label,str) and process_label.startswith('system_u:system_r:container_t:') and label.split(':',3)[-1]==process_label.split(':',3)[-1] and all(os.getxattr(source,'security.selinux').rstrip(b'\0').decode()==label for source,_ in expected))
                image_ok=bool(podman_ok)
                caps_ok=set(host['CapDrop']) >= {'CHOWN','DAC_OVERRIDE','FOWNER','FSETID','KILL','NET_BIND_SERVICE','SETFCAP','SETGID','SETPCAP','SETUID','SYS_CHROOT'}
            else:
                mounts=host['Mounts']
                actual={(item['Source'],item['Target']) for item in mounts if item['Type']=='bind'}
                image_ok=config['Image']==self.supervisor.config.image
                caps_ok='ALL' in host['CapDrop']
            env=config.get('Env') or []
            safe_env=all(isinstance(item,str) and not any(word in item.split('=',1)[0].upper() for word in ('TOKEN','SECRET','PASSWORD','CREDENTIAL','API_KEY')) for item in env)
            expected_env=('OPENFILM_NO_BROWSER=1','OPENFILM_NO_UPDATE_CHECK=1','OPENFILM_HOME=/tmp/openfilm','PLAYWRIGHT_BROWSERS_PATH=/opt/openfilm-browsers')
            safe_env=safe_env and all([item for item in env if item.split('=',1)[0]==entry.split('=',1)[0]]==[entry] for entry in expected_env)
            return bool(status==200 and details['Id']==cid and config['Labels']['agent-workflows.run']==assignment and image_ok and config['Entrypoint']==['node','/app/runner/openfilm_render.mjs'] and config['Cmd']==[] and config['User']==str(os.geteuid())+':'+str(os.getegid()) and safe_env and len(mounts)==2 and actual==expected and host['NetworkMode']=='none' and host['ReadonlyRootfs'] is True and caps_ok and not host.get('CapAdd') and host.get('Privileged') is False and any(x in ('no-new-privileges','no-new-privileges:true') for x in host['SecurityOpt']) and host['RestartPolicy']['Name']=='no' and host['AutoRemove'] is False and state['Running'] is False and state['Status']=='exited' and type(state['ExitCode']) is int and state['ExitCode']==terminal['exit_code'] and result['status']==('exited' if state['ExitCode']==0 else 'failed') and self.verify_policy(self.policy_hash) is True)
        except Exception: return False
