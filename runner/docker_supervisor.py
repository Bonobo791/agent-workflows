"""Durable exact-container termination coordinator.

This component does not yet provide a runnable job supervisor or establish
host isolation. Ambiguous operations remain fenced for operator inspection.
"""
from runner.docker_controller import DockerController, DockerError

class DockerSupervisor:
    def __init__(self,assignments,config=None,fence_ready=None,provider_ready=None,session_root=None,credential_delivery=None):
        self.assignments=assignments
        self.config=config
        self.fence_ready=fence_ready
        self.provider_ready=provider_ready
        self.session_root=session_root
        self.credential_delivery=credential_delivery
        self.db_path=assignments.db.execute('PRAGMA database_list').fetchone()[2]
        if not self.db_path: raise DockerError('durable database required')
        assignments.db.execute('CREATE TABLE IF NOT EXISTS docker_terminal_evidence(run TEXT PRIMARY KEY,container_id TEXT NOT NULL,status TEXT NOT NULL,observed_at REAL NOT NULL)')
        assignments.db.execute('CREATE TABLE IF NOT EXISTS docker_assignment_policy(run TEXT PRIMARY KEY,policy_hash TEXT NOT NULL)')
        assignments.db.execute('CREATE TABLE IF NOT EXISTS docker_disposable_assignments(run TEXT PRIMARY KEY,container_id TEXT NOT NULL UNIQUE,policy_hash TEXT NOT NULL)')
        assignments.db.execute('CREATE TABLE IF NOT EXISTS docker_execution_map(task_run TEXT NOT NULL,role TEXT NOT NULL,assignment TEXT PRIMARY KEY)')
    def preflight(self):
        import sqlite3
        if self.config is None or not callable(self.fence_ready) or self.fence_ready(None) is not True or not callable(self.provider_ready) or self.provider_ready() is not True: return False
        db=sqlite3.connect(self.db_path)
        try:
            return bool(db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='docker_config' AND version=? AND status='approved'",(self.config.policy_hash,)).fetchone())
        except sqlite3.Error: return False
        finally: db.close()
    @staticmethod
    def _skill_digest(skills,model):
        import hashlib, json
        from runner.adapter import app_server_command
        if not isinstance(model,str) or not model or not isinstance(skills,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in skills.items()): raise DockerError('invalid skill or model binding')
        payload={'skills':skills,'model':model,'effort':'medium','command':app_server_command()}
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def can_resume(self,run,role,session,skills=None,model=None):
        from runner.session_store import SessionStore
        delivery=self.credential_delivery
        if self.session_root is None or self.config is None or delivery is None or not callable(getattr(delivery,'ready',None)) or delivery.ready() is not True: return False
        try:
            store=SessionStore(self.session_root)
            try: return store.can_resume(run,role,session,self.config.policy_hash,self._skill_digest(skills,model),delivery.verify_home)
            finally: store.close()
        except Exception: return False
    def start_execution(self,run,role,session,skills,model):
        delivery=self.credential_delivery
        if self.session_root is None or delivery is None or not callable(getattr(delivery,'ready',None)) or delivery.ready() is not True: raise DockerError('approved credential delivery unavailable')
        return self._start(run,role,session,skills,model)
    def start(self,run,role): return self._start(run,role,None,None,None)
    def _start(self,run,role,session,skills,model):
        import re, secrets, sqlite3
        from runner.docker_controller import DockerAssignments
        if not self.preflight() or not all(isinstance(x,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',x) for x in (run,role)) or self.fence_ready(run) is not True: raise DockerError('approved runtime and active fenced task required')
        assignment='job-'+secrets.token_hex(24)
        db=sqlite3.connect(self.db_path,timeout=10,isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                import time
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone() and not db.execute("SELECT 1 FROM jobs WHERE run=? AND status='running' AND expires>?",(run,time.time())).fetchone(): raise DockerError('task claim unavailable')
                if db.execute("SELECT 1 FROM docker_execution_map m LEFT JOIN docker_assignments a ON a.run=m.assignment WHERE m.task_run=? AND (a.run IS NULL OR a.state!='terminated')",(run,)).fetchone(): raise DockerError('prior execution unresolved')
                db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',(run,role,assignment)); db.execute('COMMIT')
            except Exception: db.execute('ROLLBACK'); raise
        finally: db.close()
        session_binding=None
        if skills is not None:
            from runner.session_store import SessionStore
            store=SessionStore(self.session_root)
            try:
                digest=self._skill_digest(skills,model)
                entry=store.acquire(run,role,assignment,self.config.policy_hash,digest,session=session,verify_safe=self.credential_delivery.verify_home)
                session_binding={'home':entry['home'],'task':run,'role':role,'skills':digest}
            finally: store.close()
        mounts=self.config.create_job_dirs(assignment)
        workspace_binding=None
        import sqlite3
        check=sqlite3.connect(self.db_path)
        try:
            table=check.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_runs'").fetchone()
            workflow=check.execute('SELECT workflow FROM graph_runs WHERE id=?',(run,)).fetchone() if table else None
        finally: check.close()
        if workflow==('engineering',) and role in ('implement','test','qa'):
            from runner.engineering_workspace import EngineeringWorkspace
            try:
                registry=EngineeringWorkspace(self.config.job_root.parent)
                try: mounts['workspace']=registry.claim(run,assignment,self.config.policy_hash)
                finally: registry.close()
            except Exception: raise DockerError('verified engineering workspace unavailable; assignment fenced') from None
            workspace_binding={'task':run,'role':role}
        assignments=DockerAssignments(self.db_path,self.assignments.engine,self.config.image)
        try:
            cid=assignments.create_disposable(assignment,self.config,mounts,session=session_binding,workspace_binding=workspace_binding)
            if session_binding is not None and self.credential_delivery.deliver(assignment,cid) is not True: raise DockerError('credential delivery unverified')
            if self.fence_ready(run) is not True: raise DockerError('task claim lost before start')
            assignments.start(assignment)
            if self.fence_ready(run) is not True: raise DockerError('task cancelled after start; termination required')
        finally: assignments.close()
        return DockerHandle(self,run,assignment,mounts['output'],session_binding is not None,workspace_binding is not None)
    def finish(self,run,*,kill=False):
        import sqlite3
        db=sqlite3.connect(self.db_path,timeout=10,isolation_level=None)
        try: return self._finish(db,run,kill)
        finally: db.close()
    def _finish(self,db,run,kill):
        row=db.execute('SELECT container_id,state FROM docker_assignments WHERE run=?',(run,)).fetchone()
        if not row or row[1]!='running' or not row[0]: raise DockerError('running assignment unavailable')
        controller=DockerController(self.assignments.engine,{run:row[0]})
        controller._inspect(run)
        changed=db.execute("UPDATE docker_assignments SET state='stopping' WHERE run=? AND container_id=? AND state='running'",(run,row[0]))
        if changed.rowcount!=1: raise DockerError('assignment changed')
        try:
            if controller.stop(run,kill=kill) is not True: raise DockerError('terminal state unavailable')
            import time
            db.execute('BEGIN IMMEDIATE')
            try:
                changed=db.execute("UPDATE docker_assignments SET state='terminated' WHERE run=? AND container_id=? AND state='stopping'",(run,row[0]))
                if changed.rowcount!=1: raise DockerError('assignment changed')
                db.execute('INSERT INTO docker_terminal_evidence VALUES(?,?,?,?)',(run,row[0],'exited',time.time()))
                db.execute('COMMIT')
            except Exception:
                db.execute('ROLLBACK'); raise
            return True
        except Exception: raise DockerError('termination uncertain; inspect before retry') from None

    def escalation_version(self,run,policy_hash):
        import hashlib, json, re, sqlite3
        if not isinstance(policy_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',policy_hash): raise DockerError('exact policy hash required')
        db=sqlite3.connect(self.db_path)
        try: row=db.execute('SELECT a.container_id,a.state,p.policy_hash FROM docker_assignments a JOIN docker_assignment_policy p ON p.run=a.run WHERE a.run=?',(run,)).fetchone()
        finally: db.close()
        if not row or row[1]!='stopping' or not row[0] or row[2]!=policy_hash: raise DockerError('uncertain stop unavailable')
        return hashlib.sha256(json.dumps({'run':run,'container_id':row[0],'policy_hash':policy_hash},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def escalate(self,run,ledger,policy_hash,verify_quiescent=None):
        import sqlite3, time
        version=self.escalation_version(run,policy_hash)
        if ledger.db.execute('PRAGMA database_list').fetchone()[2] != self.db_path: raise DockerError('approval ledger must share durable state')
        db=sqlite3.connect(self.db_path,timeout=10,isolation_level=None)
        try:
            row=db.execute("SELECT container_id FROM docker_assignments WHERE run=? AND state='stopping'",(run,)).fetchone()
            if not row: raise DockerError('uncertain stop unavailable')
            controller=DockerController(self.assignments.engine,{run:row[0]})
            controller._inspect(run)
            if verify_quiescent is None:
                disposable=db.execute('SELECT 1 FROM docker_disposable_assignments WHERE run=? AND container_id=? AND policy_hash=?',(run,row[0],policy_hash)).fetchone()
                try:
                    status,details=self.assignments.engine.request('GET','/containers/'+row[0]+'/json')
                    valid=(status==200 and details['Id']==row[0] and details['Config']['Labels']['agent-workflows.run']==run and details['HostConfig']['RestartPolicy']['Name']=='no' and details['HostConfig']['AutoRemove'] is False)
                except Exception: valid=False
                if not disposable or not valid: raise DockerError('disposable exact-container policy unverified')
            elif not callable(verify_quiescent) or verify_quiescent(run,row[0]) is not True: raise DockerError('prior stop request quiescence unverified')
            scope='docker-container:'+row[0]
            try: ledger.reserve_scoped(run,'docker_kill',version,scope)
            except Exception: raise DockerError('exact kill approval required') from None
            changed=db.execute("UPDATE docker_assignments SET state='escalating' WHERE run=? AND container_id=? AND state='stopping'",(run,row[0]))
            if changed.rowcount!=1:
                ledger.resolve_scoped(run,'docker_kill',version,scope,'not_executed')
                raise DockerError('assignment changed')
            try:
                if controller.stop(run,kill=True) is not True: raise DockerError('termination unverified')
                db.execute('BEGIN IMMEDIATE')
                try:
                    changed=db.execute("UPDATE docker_assignments SET state='terminated' WHERE run=? AND container_id=? AND state='escalating'",(run,row[0]))
                    if changed.rowcount!=1: raise DockerError('assignment changed')
                    db.execute('INSERT INTO docker_terminal_evidence VALUES(?,?,?,?)',(run,row[0],'exited',time.time()))
                    db.execute('COMMIT')
                except Exception: db.execute('ROLLBACK'); raise
                ledger.resolve_scoped(run,'docker_kill',version,scope,'executed')
                return True
            except Exception: raise DockerError('kill outcome uncertain; inspect before retry') from None
        finally: db.close()

class DockerHandle:
    def __init__(self,supervisor,run,assignment,output_dir,has_session=False,has_workspace=False):
        self.supervisor,self.run,self.assignment,self.output_dir=supervisor,run,assignment,output_dir
        self.has_session=has_session
        self.has_workspace=has_workspace
        self.sock=None
    def assert_active(self):
        import sqlite3
        if self.supervisor.fence_ready(self.run) is not True: raise DockerError('task fence unavailable')
        db=sqlite3.connect(self.supervisor.db_path)
        try: row=db.execute('SELECT state FROM docker_assignments WHERE run=?',(self.assignment,)).fetchone()
        finally: db.close()
        if row!=('running',): raise DockerError('container assignment unavailable')
    def open_transport(self,deadline,cancelled):
        self.assert_active()
        from runner.docker_controller import DockerAssignments
        from runner.docker_attach import DockerAttachTransport
        assignment=DockerAssignments(self.supervisor.db_path,self.supervisor.assignments.engine,self.supervisor.config.image)
        try: self.sock=assignment.attach(self.assignment)
        finally: assignment.close()
        try: self.assert_active()
        except Exception: self.sock.close(); raise
        return DockerAttachTransport(self.sock,deadline,lambda: cancelled() or self.supervisor.fence_ready(self.run) is not True)
    def bind_session(self,session):
        if self.has_session:
            from runner.session_store import SessionStore
            store=SessionStore(self.supervisor.session_root)
            try: store.bind(self.assignment,session)
            finally: store.close()
    def finish(self):
        try:
            if self.supervisor.finish(self.assignment) is not True: return False
            if self.has_workspace:
                from runner.engineering_workspace import EngineeringWorkspace
                registry=EngineeringWorkspace(self.supervisor.config.job_root.parent)
                try: registry.release(self.run,self.assignment)
                finally: registry.close()
            if self.has_session:
                from runner.session_store import SessionStore
                import sqlite3
                def stopped(job):
                    db=sqlite3.connect(self.supervisor.db_path)
                    try: return bool(db.execute("SELECT 1 FROM docker_assignments a JOIN docker_terminal_evidence e ON e.run=a.run AND e.container_id=a.container_id WHERE a.run=? AND a.state='terminated'",(job,)).fetchone())
                    finally: db.close()
                store=SessionStore(self.supervisor.session_root)
                try: store.release(self.assignment,stopped)
                finally: store.close()
            return True
        finally:
            if self.sock is not None: self.sock.close()
    def cancel(self):
        if self.sock is not None: self.sock.close()
