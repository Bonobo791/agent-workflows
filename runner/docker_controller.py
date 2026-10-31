"""Exact-container Docker Engine stop contract; transport and records are trusted.

This is not an authorization boundary or a complete worker supervisor. The
controller must be isolated from jobs, and the Engine must enforce permissions.
"""
import re

class DockerError(RuntimeError): pass

class DockerController:
    def __init__(self, engine, records):
        if not isinstance(records, dict) or any(not isinstance(run,str) or not run or not isinstance(cid,str) or not re.fullmatch(r'[0-9a-f]{64}',cid) for run,cid in records.items()):
            raise DockerError('exact recorded container IDs required')
        self.engine=engine
        self.records=dict(records)

    def _inspect(self,run):
        cid=self.records.get(run)
        if cid is None: raise DockerError('recorded container unavailable')
        try:
            status, data=self.engine.request('GET','/containers/'+cid+'/json')
            state=data['State']
            if (status!=200 or data['Id']!=cid or data['Config']['Labels'].get('agent-workflows.run')!=run
                or type(state['Running']) is not bool or not isinstance(state['Status'],str)):
                raise DockerError('container identity or state mismatch')
            return cid,state
        except DockerError: raise
        except Exception: raise DockerError('container inspection failed') from None

    def stop(self,run,*,kill=False):
        cid,state=self._inspect(run)
        if not state['Running']:
            if state['Status']!='exited': raise DockerError('container state is not terminal')
            return True
        action='kill?signal=SIGKILL' if kill else 'stop?t=10'
        try:
            status,_=self.engine.request('POST','/containers/'+cid+'/'+action)
            if status not in (204,304): raise DockerError('stop outcome uncertain')
            status,result=self.engine.request('POST','/containers/'+cid+'/wait?condition=not-running')
            if status!=200 or not isinstance(result,dict) or type(result.get('StatusCode')) is not int or result.get('Error'):
                raise DockerError('wait outcome uncertain')
            _,state=self._inspect(run)
            if state['Running'] or state['Status']!='exited': raise DockerError('container termination unverified')
            return True
        except DockerError: raise
        except Exception: raise DockerError('stop outcome uncertain; inspect before retry') from None

class UnixEngine:
    """Bounded HTTP client for a separately provisioned local Engine socket."""
    def __init__(self,path,timeout=15):
        if not isinstance(path,str) or not path.startswith('/') or '\x00' in path or timeout<=0: raise DockerError('absolute Engine socket and positive timeout required')
        self.path,self.timeout=path,timeout

    def attach(self,cid):
        import socket
        import time
        if not isinstance(cid,str) or not re.fullmatch(r'[0-9a-f]{64}',cid): raise DockerError('exact container ID required')
        sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); sock.settimeout(self.timeout)
        deadline=time.monotonic()+self.timeout
        try:
            sock.connect(self.path)
            target='/v1.49/containers/'+cid+'/attach?stream=1&stdin=1&stdout=1&stderr=1'
            request=('POST '+target+' HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Length: 0\r\n\r\n').encode('ascii')
            sock.sendall(request)
            header=b''
            while not header.endswith(b'\r\n\r\n'):
                if len(header)>=16384: raise DockerError('attach response headers too large')
                remaining=deadline-time.monotonic()
                if remaining<=0: raise DockerError('attach upgrade timed out')
                sock.settimeout(remaining)
                part=sock.recv(1)
                if not part: raise DockerError('attach response incomplete')
                header+=part
            lines=header[:-4].decode('ascii').split('\r\n')
            if not re.fullmatch(r'HTTP/1\.[01] 101(?: .*)?',lines[0]): raise DockerError('attach upgrade rejected')
            fields={}
            for line in lines[1:]:
                key,value=line.split(':',1); key=key.strip().lower()
                if key in fields: raise DockerError('duplicate attach header')
                fields[key]=value.strip().lower()
            if 'upgrade' not in [x.strip() for x in fields.get('connection','').split(',')] or fields.get('upgrade')!='tcp' or fields.get('content-type','').split(';',1)[0] not in ('application/vnd.docker.raw-stream','application/vnd.docker.multiplexed-stream'): raise DockerError('invalid attach upgrade')
            return sock
        except Exception:
            sock.close(); raise DockerError('attach upgrade failed') from None

    def logs(self,cid,limit):
        import http.client, socket, struct
        if not isinstance(cid,str) or not re.fullmatch(r'[0-9a-f]{64}',cid) or type(limit) is not int or limit<1: raise DockerError('invalid log request')
        class Connection(http.client.HTTPConnection):
            def connect(connection):
                connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); connection.sock.settimeout(self.timeout); connection.sock.connect(self.path)
        connection=Connection('localhost',timeout=self.timeout)
        try:
            connection.request('GET','/v1.49/containers/'+cid+'/logs?stdout=1&stderr=1')
            response=connection.getresponse()
            if response.status!=200: raise DockerError('container logs unavailable')
            stdout=bytearray(); stderr=bytearray(); frames=0
            while True:
                head=response.read(8)
                if not head: break
                if len(head)!=8 or head[1:4]!=b'\0\0\0' or head[0] not in (1,2): raise DockerError('invalid container log frame')
                size=struct.unpack('>I',head[4:])[0]; target=stdout if head[0]==1 else stderr
                frames+=1
                if frames>10000 or size>limit-len(target): raise DockerError('container log limit exceeded')
                payload=response.read(size)
                if len(payload)!=size: raise DockerError('incomplete container log frame')
                target.extend(payload)
            return bytes(stdout),bytes(stderr)
        except Exception: raise DockerError('container logs unavailable or incomplete') from None
        finally: connection.close()

    def request(self,method,path,body=None,*,timeout=None):
        import http.client
        import json
        import math
        import socket
        import time
        if not isinstance(path,str) or not ((method=='POST' and path=='/containers/create') or (method=='GET' and re.fullmatch(r'/containers/[0-9a-f]{64}/json',path)) or (method=='POST' and re.fullmatch(r'/containers/[0-9a-f]{64}/(?:stop\?t=10|kill\?signal=SIGKILL|wait\?condition=not-running|start)',path))):
            raise DockerError('unsupported Engine request')
        if (path=='/containers/create') != isinstance(body,dict): raise DockerError('invalid Engine request body')
        if timeout is not None and (not path.endswith('/wait?condition=not-running') or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=600): raise DockerError('invalid Engine wait timeout')
        request_timeout=self.timeout if timeout is None else timeout
        deadline=None if timeout is None else time.monotonic()+timeout
        class DeadlineSocket(socket.socket):
            def remaining(sock):
                if deadline is not None:
                    remaining=deadline-time.monotonic()
                    if remaining<=0: raise TimeoutError('Engine wait deadline exceeded')
                    sock.settimeout(remaining)
            def connect(sock,address):
                sock.remaining(); return super().connect(address)
            def sendall(sock,data,*args):
                sock.remaining(); return super().sendall(data,*args)
            def recv_into(sock,buffer,*args):
                sock.remaining(); return super().recv_into(buffer,*args)
        class Connection(http.client.HTTPConnection):
            def connect(connection):
                connection.sock=DeadlineSocket(socket.AF_UNIX,socket.SOCK_STREAM)
                connection.sock.settimeout(request_timeout)
                connection.sock.connect(self.path)
        connection=Connection('localhost',timeout=request_timeout)
        try:
            payload=json.dumps(body,separators=(',',':')).encode() if body is not None else (b'' if method=='POST' else None)
            connection.request(method,'/v1.49'+path,body=payload,headers={'Content-Type':'application/json'} if body is not None else {})
            response=connection.getresponse(); payload=response.read(1048577)
            if len(payload)>1048576: raise DockerError('Engine response too large')
            result=json.loads(payload) if payload else None
            if deadline is not None and time.monotonic()>=deadline: raise DockerError('Engine wait deadline exceeded')
            return response.status,result
        except DockerError: raise
        except Exception: raise DockerError('Engine request outcome uncertain') from None
        finally: connection.close()


class DockerAssignments:
    """Persist launch intent before contacting Engine; ambiguous calls stay fenced."""
    def __init__(self,path,engine,image):
        import sqlite3
        if not isinstance(image,str) or not re.fullmatch(r'(?:[A-Za-z0-9._/:@-]+@)?sha256:[0-9a-f]{64}',image): raise DockerError('immutable image digest required')
        self.engine,self.image=engine,image
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None)
        self.db.execute('CREATE TABLE IF NOT EXISTS docker_assignments(run TEXT PRIMARY KEY, container_id TEXT UNIQUE, state TEXT NOT NULL, image TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS docker_assignment_policy(run TEXT PRIMARY KEY,policy_hash TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS docker_disposable_assignments(run TEXT PRIMARY KEY,container_id TEXT NOT NULL UNIQUE,policy_hash TEXT NOT NULL)')
    def close(self): self.db.close()
    def get(self,run):
        row=self.db.execute('SELECT container_id,state FROM docker_assignments WHERE run=?',(run,)).fetchone()
        return {'id':row[0],'state':row[1]} if row else None
    def create(self,run):
        if not isinstance(run,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',run): raise DockerError('valid run required')
        try: self.db.execute("INSERT INTO docker_assignments VALUES(?,NULL,'creating',?)",(run,self.image))
        except Exception: raise DockerError('assignment exists or cannot be reserved') from None
        spec={'Image':self.image,'Labels':{'agent-workflows.run':run},'OpenStdin':True,'AttachStdin':True,'AttachStdout':True,'AttachStderr':True,'Tty':False,
              'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'], 'RestartPolicy':{'Name':'no'}}}
        try:
            status,result=self.engine.request('POST','/containers/create',spec)
            cid=result['Id']
            if status!=201 or not isinstance(cid,str) or not re.fullmatch(r'[0-9a-f]{64}',cid): raise DockerError('create outcome uncertain')
            self.db.execute("UPDATE docker_assignments SET container_id=?,state='created' WHERE run=? AND state='creating'",(cid,run))
            return cid
        except Exception: raise DockerError('create outcome uncertain; inspect before retry') from None
    def create_disposable(self,run,config,mounts,session=None,workspace_binding=None,command=None,push=None,mutation=None,openfilm=None):
        from runner.docker_config import DockerConfig
        from runner.adapter import app_server_command
        from pathlib import Path
        import os, stat
        if not isinstance(config,DockerConfig) or config.image!=self.image or not isinstance(run,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',run) or run in ('.','..'): raise DockerError('invalid disposable assignment')
        if config.engine=='podman-rootless' and openfilm is None: raise DockerError('rootless Podman policy is restricted to OpenFilm rendering')
        expected={'workspace':str(config.job_root/run/'workspace'),'output':str(config.job_root/run/'output')}
        if workspace_binding is not None:
            from runner.engineering_workspace import EngineeringWorkspace
            if not isinstance(workspace_binding,dict) or set(workspace_binding)!={'task','role'} or workspace_binding['role'] not in ('implement','test','qa','push'): raise DockerError('invalid workspace binding')
            registry=EngineeringWorkspace(config.job_root.parent)
            try:
                row=registry.db.execute('SELECT repository,base,branch,owner,path,policy,assignment FROM engineering_workspaces WHERE run=?',(workspace_binding['task'],)).fetchone()
                mapping=registry.db.execute('SELECT task_run,role FROM docker_execution_map WHERE assignment=?',(run,)).fetchone()
                approval=registry.db.execute('SELECT binding_hash FROM engineering_workspace_approvals WHERE run=?',(workspace_binding['task'],)).fetchone()
                if not row or approval!=(registry.binding_hash(workspace_binding['task'],*row[:4],config.policy_hash),): raise DockerError('workspace approval mismatch')
                if mapping!=(workspace_binding['task'],workspace_binding['role']) or not row or row[5:]!=(config.policy_hash,run) or registry.verify(workspace_binding['task'],*row[:4],config.policy_hash)!=row[4]: raise DockerError('workspace binding mismatch')
                expected['workspace']=row[4]
            finally: registry.close()
        if workspace_binding is not None and workspace_binding['role']=='push' and push is None: raise DockerError('push specification required')
        exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docker_execution_map'").fetchone()
        role=self.db.execute('SELECT role FROM docker_execution_map WHERE assignment=?',(run,)).fetchone() if exists else None
        if role==('pr_mutation',) and mutation is None: raise DockerError('mutation specification required')
        if role==('openfilm_render',) and openfilm is None: raise DockerError('render specification required')
        if openfilm is not None:
            if os.geteuid()==0: raise DockerError('render controller must be unprivileged')
            if session is not None or workspace_binding is not None or command is not None or push is not None or mutation is not None or not isinstance(openfilm,dict) or set(openfilm)!={'task','attempt_id','policy_hash'} or role!=('openfilm_render',): raise DockerError('invalid render assignment')
            row=self.db.execute("SELECT task_run,attempt_id,policy_hash,state FROM openfilm_render_jobs WHERE assignment=?",(run,)).fetchone()
            mapping=self.db.execute('SELECT task_run,role FROM docker_execution_map WHERE assignment=?',(run,)).fetchone()
            approved=self.db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='openfilm_policy' AND version=? AND status='approved'",(openfilm['policy_hash'],)).fetchone()
            if row!=(openfilm['task'],openfilm['attempt_id'],openfilm['policy_hash'],'launching') or mapping!=(openfilm['task'],'openfilm_render') or not approved: raise DockerError('approved render binding unavailable')
        if mounts!=expected: raise DockerError('unexpected mount paths')
        session_source=None
        if session is not None:
            from runner.session_store import SessionStore
            if not isinstance(session,dict) or set(session)!={'home','task','role','skills'}: raise DockerError('invalid session binding')
            store=SessionStore(config.job_root.parent)
            try:
                row=store.db.execute("SELECT h.id,h.run,h.role,h.config,h.skills,h.state FROM homes h JOIN home_claims c ON c.home=h.id WHERE c.job=? AND c.state='active'",(run,)).fetchone()
                if not row or row[1:]!=(session['task'],session['role'],config.policy_hash,session['skills'],'active') or session['home']!=str(store.homes/row[0]): raise DockerError('session binding mismatch')
                store._check_home(row[0]); session_source=session['home']
            finally: store.close()
        try:
            for value in expected.values():
                path=Path(value); info=path.stat()
                if path.resolve(strict=True)!=path or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077: raise DockerError('unsafe job mount')
            db=self.db; db.execute('BEGIN IMMEDIATE')
            try:
                if not db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='docker_config' AND version=? AND status='approved'",(config.policy_hash,)).fetchone(): raise DockerError('exact configuration approval required')
                db.execute("INSERT INTO docker_assignments VALUES(?,NULL,'creating',?)",(run,self.image))
                db.execute('INSERT INTO docker_assignment_policy VALUES(?,?)',(run,config.policy_hash))
                db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
        except Exception: raise DockerError('assignment or approval unavailable') from None
        spec={'Image':self.image,'Labels':{'agent-workflows.run':run},'Cmd':app_server_command(),'WorkingDir':'/workspace','OpenStdin':True,'AttachStdin':True,'AttachStdout':True,'AttachStderr':True,'Tty':False,
              'HostConfig':{'NetworkMode':config.network,'ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'RestartPolicy':{'Name':'no'},'AutoRemove':False,
                            'Mounts':[{'Type':'bind','Source':expected['workspace'],'Target':'/workspace'},{'Type':'bind','Source':expected['output'],'Target':'/output'}]}}
        if openfilm is not None:
            spec['Entrypoint']=['node','/app/runner/openfilm_render.mjs']; spec['Cmd']=[]
            spec['User']=str(os.geteuid())+':'+str(os.getegid())
            spec['OpenStdin']=False; spec['AttachStdin']=False
            spec['Env']=['OPENFILM_NO_BROWSER=1','OPENFILM_NO_UPDATE_CHECK=1','OPENFILM_HOME=/tmp/openfilm','PLAYWRIGHT_BROWSERS_PATH=/opt/openfilm-browsers']
            spec['HostConfig']['NetworkMode']='none'
            spec['HostConfig']['Tmpfs']={'/tmp':'rw,nosuid,nodev,size=1073741824'}
            if config.engine=='podman-rootless':
                spec['HostConfig']['UsernsMode']='keep-id'
                del spec['HostConfig']['Mounts']
                spec['HostConfig']['Binds']=[expected['workspace']+':/workspace:rw,Z',expected['output']+':/output:rw,Z']
        if command is not None:
            import hashlib, json
            if session is not None or workspace_binding is None or workspace_binding['role']!='test' or not isinstance(command,dict) or set(command)!={'argv','timeout_seconds','output_limit'}: raise DockerError('invalid command assignment')
            policy=hashlib.sha256(json.dumps(command,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            row=self.db.execute("SELECT run,policy_hash,state FROM supervised_commands WHERE id=?",(run,)).fetchone()
            approval=self.db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='command_policy' AND version=? AND status='approved'",(policy,)).fetchone()
            if row!=(workspace_binding['task'],policy,'uncertain') or not approval: raise DockerError('approved command binding unavailable')
            spec['Entrypoint']=[command['argv'][0]]; spec['Cmd']=command['argv'][1:]; spec['OpenStdin']=False; spec['AttachStdin']=False; spec['HostConfig']['NetworkMode']='none'
        if push is not None:
            from runner.docker_push import push_argv
            if session is not None or command is not None or workspace_binding is None or workspace_binding['role']!='push' or config.network=='none' or not isinstance(push,dict) or set(push)!={'repo','branch','head','action','expected_remote'}: raise DockerError('invalid isolated push assignment')
            attempt=self.db.execute('SELECT task_run,repo,branch,head,action,state FROM git_push_attempts WHERE assignment=?',(run,)).fetchone()
            if attempt!=(workspace_binding['task'],push['repo'],push['branch'],push['head'],push['action'],'reserved'): raise DockerError('push assignment mismatch')
            scope='github:'+push['repo']+':branch:'+push['branch']
            action=self.db.execute("SELECT 1 FROM external_actions a JOIN action_scopes s ON s.run=a.run AND s.action=a.action AND s.version=a.version WHERE a.run=? AND a.action=? AND a.version=? AND a.status='uncertain' AND s.scope=?",(workspace_binding['task'],push['action'],push['head'],scope)).fetchone()
            if not action: raise DockerError('scoped push approval unavailable')
            from workflows.engineering.github import GitHub
            owner,repo=push['repo'].split('/')
            if GitHub(None,owner,repo).push_action(push['branch'],push['expected_remote'])!=push['action']: raise DockerError('push expected ref approval mismatch')
            argv=push_argv(push['repo'],push['branch'],push['head'],push['expected_remote'])
            spec['Entrypoint']=[argv[0]]; spec['Cmd']=argv[1:]; spec['OpenStdin']=False; spec['AttachStdin']=False
        if mutation is not None:
            if session is not None or command is not None or push is not None or workspace_binding is not None or config.network=='none' or not isinstance(mutation,dict) or set(mutation)!={'task','action','scope','kind'} or mutation['kind'] not in ('draft','ready'): raise DockerError('invalid mutation assignment')
            row=db.execute("SELECT p.run,p.action,p.scope,p.kind,m.role FROM pr_mutation_processes p JOIN docker_execution_map m ON m.assignment=p.assignment AND m.task_run=p.run WHERE p.assignment=?",(run,)).fetchone()
            if row!=(mutation['task'],mutation['action'],mutation['scope'],mutation['kind'],'pr_mutation'): raise DockerError('mutation assignment mismatch')
            approved=db.execute("SELECT 1 FROM external_actions a JOIN action_scopes s ON s.run=a.run AND s.action=a.action AND s.version=a.version WHERE a.run=? AND a.action=? AND a.status='uncertain' AND s.scope=?",(mutation['task'],mutation['action'],mutation['scope'])).fetchone()
            if not approved: raise DockerError('scoped mutation approval unavailable')
            spec['Entrypoint']=['python','-m','runner.pr_mutation_helper']; spec['Cmd']=[]
            spec['HostConfig']['Mounts']=[]
        if session_source is not None:
            spec['Env']=['CODEX_HOME=/codex-home','CODEX_SQLITE_HOME=/codex-home']
            spec['HostConfig']['Mounts'].append({'Type':'bind','Source':session_source,'Target':'/codex-home'})
        try:
            status,result=self.engine.request('POST','/containers/create',spec); cid=result['Id']
            if status!=201 or not isinstance(cid,str) or not re.fullmatch(r'[0-9a-f]{64}',cid): raise DockerError('create outcome uncertain')
            db.execute('BEGIN IMMEDIATE')
            try:
                cur=db.execute("UPDATE docker_assignments SET container_id=?,state='created' WHERE run=? AND state='creating'",(cid,run))
                if cur.rowcount!=1: raise DockerError('assignment changed')
                db.execute('INSERT INTO docker_disposable_assignments VALUES(?,?,?)',(run,cid,config.policy_hash))
                db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
            return cid
        except Exception: raise DockerError('create outcome uncertain; inspect before retry') from None
    def attach(self,run):
        row=self.db.execute('SELECT container_id,state,image FROM docker_assignments WHERE run=?',(run,)).fetchone()
        if not row or row[1] not in ('created','running') or row[2]!=self.image: raise DockerError('attach assignment unavailable')
        cid,state=DockerController(self.engine,{run:row[0]})._inspect(run)
        if state['Status'] not in ('created','running'): raise DockerError('attach container unavailable')
        return self.engine.attach(cid)
    def start(self,run,allow_completed=False):
        row=self.db.execute('SELECT container_id,state,image FROM docker_assignments WHERE run=?',(run,)).fetchone()
        if not row or row[1]!='created' or row[2]!=self.image: raise DockerError('created assignment unavailable')
        cid=row[0]
        controller=DockerController(self.engine,{run:cid})
        _,state=controller._inspect(run)
        if state['Running'] or state['Status']!='created': raise DockerError('container is not in created state')
        cursor=self.db.execute("UPDATE docker_assignments SET state='starting' WHERE run=? AND state='created'",(run,))
        if cursor.rowcount!=1: raise DockerError('assignment changed')
        try:
            status,_=self.engine.request('POST','/containers/'+cid+'/start')
            if status!=204: raise DockerError('start outcome uncertain')
            _,state=controller._inspect(run)
            if not (state['Running'] and state['Status']=='running') and not (allow_completed and not state['Running'] and state['Status']=='exited' and type(state.get('ExitCode')) is int): raise DockerError('start outcome uncertain')
            self.db.execute("UPDATE docker_assignments SET state='running' WHERE run=? AND state='starting'",(run,))
            return True
        except Exception: raise DockerError('start outcome uncertain; inspect before retry') from None
