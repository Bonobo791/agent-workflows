"""Owner-only Unix socket control. It intentionally has no live execution endpoint."""
import hmac
import json
import os
import socketserver
import stat
from pathlib import Path

MAX_CONTROL_RESPONSE=6*1024*1024+65536

class ControlError(RuntimeError): pass

class ControlPlane:
    def __init__(self,service,secret,fixture_mode=False):
        if not isinstance(secret,str) or not secret: raise ValueError('control secret required')
        self.service,self.secret,self.fixture_mode=service,secret,fixture_mode
        self.auto_worker=None
    def handle(self,secret,request):
        if not isinstance(secret,str) or not hmac.compare_digest(secret,self.secret): raise ControlError('unauthorized')
        if not isinstance(request,dict): raise ControlError('invalid request')
        action=request.get('action')
        try:
            if action=='start_configured_auto':
                if self.auto_worker is None: raise ControlError('automatic worker unavailable')
                return self.auto_worker.start()
            if action=='human_review_decision_auto':
                if self.auto_worker is None: raise ControlError('automatic worker unavailable')
                return self.auto_worker.request_decision(request['run'],request['version'],request['decision'])
            if action=='automatic_control':
                if self.auto_worker is None: raise ControlError('automatic worker unavailable')
                return self.auto_worker.request_action(request['run'],request['operation'],request['parameters'])
            if action=='automatic_status':
                if self.auto_worker is None: raise ControlError('automatic worker unavailable')
                return self.auto_worker.status()
            if action=='submit_configured':
                if self.service.task_intake is None or not callable(getattr(self.service.task_intake,'submit_configured',None)): raise ControlError('configured task unavailable')
                return self.service.task_intake.submit_configured()
            if action=='submit_task':
                if self.service.task_intake is None: raise ControlError('task intake unavailable')
                return self.service.task_intake.submit(request['run'],request['workflow'],request['project'],request['number'])
            if action=='manual_claim':
                from runner.manual_lifecycle import ManualLifecycle
                if not isinstance(self.service.lifecycle,ManualLifecycle): raise ControlError('manual provider unavailable')
                return {'claim':self.service.lifecycle.claim()}
            if action=='manual_confirm_stopped':
                from runner.manual_lifecycle import ManualLifecycle
                if not isinstance(self.service.lifecycle,ManualLifecycle): raise ControlError('manual provider unavailable')
                return self.service.lifecycle.confirm_operator_stop(request['run'],request['incarnation'],request['attestation'])
            if action=='stop_local_scope': return self.service.stop_local_scope(request['run'],request['unit'])
            if action=='inspect_local_scope': return self.service.inspect_local_scope(request['run'],request['unit'])
            if action=='manual_register_replacement':
                from runner.manual_lifecycle import ManualLifecycle
                if not isinstance(self.service.lifecycle,ManualLifecycle): raise ControlError('manual provider unavailable')
                return self.service.lifecycle.register_replacement(request['old_incarnation'],request['new_incarnation'],request['attestation'])
            if action=='create': self.service.create(request['run'],request['workflow'],request['task']); return {'created':True}
            if action in ('manual_stop','manual_restart','automatic_stop','automatic_restart','lifecycle_status','confirm_stopped','confirm_restarted','resolve_lifecycle_call'):
                lifecycle=self.service.lifecycle
                if lifecycle is None: raise ControlError('lifecycle supervisor unavailable')
                if action=='manual_stop': return self.service.request_lifecycle_stop(request['run'])
                if action=='manual_restart': return lifecycle.manual_restart(request['run'])
                if action=='automatic_stop': return self.service.request_lifecycle_stop(request['run'],automatic=True)
                if action=='automatic_restart': lifecycle.automatic_restart(request['run']); return lifecycle.status(request['run'])
                if action=='lifecycle_status': return lifecycle.status(request['run'])
                if action=='confirm_stopped': return self.service.confirm_lifecycle_stop(request['run'],request.get('evidence'))
                if action=='resolve_lifecycle_call': lifecycle.resolve_interrupted_call(request['run'],request.get('evidence')); return lifecycle.status(request['run'])
                if action=='confirm_restarted': lifecycle.confirm_restarted(request['run'],request.get('evidence')); return lifecycle.status(request['run'])
            if action=='lifecycle_config':
                if self.service.lifecycle is None: raise ControlError('lifecycle supervisor unavailable')
                return {'policy':dict(self.service.lifecycle.target),'policy_hash':self.service.lifecycle.policy_hash,'automatic':self.service.lifecycle.auto,'manual_available':True}
            if action=='enable_lifecycle_auto': self.service.enable_lifecycle_auto(); return {'enabled':True}
            if action=='restore_lifecycle_auto': self.service.restore_lifecycle_auto(); return {'enabled':True}
            if action=='disable_lifecycle_auto': self.service.disable_lifecycle_auto(); return {'enabled':False}
            if action=='command_policy':
                supervisor=self.service.command_supervisor
                if supervisor is None or supervisor.spec is None: raise ControlError('command policy unavailable')
                return {'policy':supervisor.spec,'policy_hash':supervisor.policy_hash}
            if action=='openfilm_policy':
                backend=self.service.openfilm_backend
                if backend is None: raise ControlError('OpenFilm policy unavailable')
                return {'policy_hash':backend.policy_hash,'image':backend.supervisor.config.image,'network':'none','version':'0.2.6'}
            if action=='openfilm_builder_policy':
                builder=getattr(self.service.openfilm_backend,'builder',None)
                if builder is None or not hasattr(builder,'policy_hash'): raise ControlError('OpenFilm builder policy unavailable')
                return {'policy_hash':builder.policy_hash}
            if action=='audio_policy':
                transcriber=self.service.audio_transcriber
                if transcriber is None: raise ControlError('audio policy unavailable')
                return {'policy_hash':transcriber.policy_hash,'languages':dict(transcriber.languages)}
            if action=='docker_escalate': return {'terminated':self.service.docker_escalate(request['run'])}
            if action in ('engineering_workspace_binding','bind_engineering_workspace'):
                import os
                from runner.engineering_workspace import EngineeringWorkspace
                service=self.service; run=request['run']; graph_claim=request['graph_claim']
                service.graph._lease(run,graph_claim)
                if not service.queue.may_start(run,request['queue_claim']): raise ControlError('queue claim unavailable')
                intake=service.task_intake; local=service.model_supervisor; supervisor=service.docker_supervisor
                if intake is None or (local is None and (supervisor is None or supervisor.config is None)) or service.graph._definition(run).name!='engineering': raise ControlError('configured engineering runtime required')
                config=intake.config
                if config['run']!=run or config['workflow']!='engineering': raise ControlError('configured task mismatch')
                if local is not None:
                    policy=local.policy_hash; workspace_root=local.engineering_root
                    if not policy or not workspace_root: raise ControlError('local engineering policy unavailable')
                else:
                    policy=supervisor.config.policy_hash; workspace_root=supervisor.config.job_root.parent
                    if not service.actions.db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='docker_config' AND version=? AND status='approved'",(policy,)).fetchone(): raise ControlError('approved Docker policy required')
                registry=EngineeringWorkspace(workspace_root)
                try:
                    repository=config['github_owner']+'/'+config['github_repo']
                    version=registry.binding_hash(run,repository,request['base'],config['github_branch'],os.geteuid(),policy)
                    if action=='engineering_workspace_binding':
                        path=registry.verify(run,repository,request['base'],config['github_branch'],os.geteuid(),policy,initial=True)
                    else:
                        path=registry.register(run,repository,request['base'],config['github_branch'],os.geteuid(),policy,request['binding_hash'])
                finally: registry.close()
                return {'registered':action=='bind_engineering_workspace','path':path,'base':request['base'],'repository':repository,'branch':config['github_branch'],'owner':os.geteuid(),'policy_hash':policy,'binding_hash':version}
            if action=='recover_correction':
                if self.service.stage_dispatcher is None: raise ControlError('stage dispatcher unavailable')
                return self.service.stage_dispatcher.recover_correction(request['run'],request['queue_claim'],request['graph_claim'])
            if action=='human_review_decision':
                if self.service.human_review is None: raise ControlError('human review unavailable')
                return self.service.human_review.decide(request['run'],request['queue_claim'],request['graph_claim'],request['version'],request['decision'])
            if action=='human_review_status':
                if self.service.human_review is None: raise ControlError('human review unavailable')
                return {'decision':self.service.human_review.current(request['run'])}
            if action in ('pr_propose','pr_approve','pr_execute','pr_review','pr_post_ready','pr_reconcile','pr_request_qa','pr_reauthorize'):
                bridge=self.service.pr_bridge
                if bridge is None: raise ControlError('PR bridge unavailable')
                args=(request['run'],request['queue_claim'],request['graph_claim'])
                if action=='pr_propose': return bridge.propose(*args,request['kind'],request['parameters'])
                if action=='pr_reauthorize': return bridge.reauthorize(*args,request['proposal'])
                if action=='pr_approve': return bridge.approve(*args,request['proposal'],request['expires'])
                if action=='pr_execute': return bridge.execute(*args,request['proposal'])
                if action=='pr_review': return bridge.review(*args,request['number'],request['dispositions'])
                if action=='pr_request_qa': return bridge.request_qa(*args,request['evidence_ref'])
                if action=='pr_post_ready': return bridge.post_ready(*args,request['number'],request['node'],request['dispositions'])
                return bridge.reconcile(*args,request['proposal'])
            if action=='dispatch_stage':
                if self.service.stage_dispatcher is None: raise ControlError('stage dispatcher unavailable')
                return self.service.stage_dispatcher.step(request['run'],request['queue_claim'],request['graph_claim'])
            if action=='enqueue': self.service.enqueue(request['run']); return {'queued':True}
            if action=='metrics':
                from runner.metrics import snapshot, prometheus_text
                metrics=prometheus_text(snapshot(self.service.graph.db))
                if len(json.dumps({'ok':True,'result':{'metrics':metrics}},separators=(',',':')).encode())+1>65536: raise ControlError('metric snapshot too large')
                return {'metrics':metrics}
            if action=='job_status': return {'job':self.service.queue.get(request['run'])}
            if action=='cancel_job': self.service.cancel_job(request['run']); return {'cancel_requested':True}
            if action=='claim': return {'claim':self.service.claim(request['run'])}
            if action=='renew': self.service.graph.renew(request['run'],request['claim'],60); return {'renewed':True}
            if action=='release': self.service.release(request['run'],request['claim']); return {'released':True}
            if action=='resume_quota': self.service.graph.resume_quota(request.get('scope','default')); return {'resumed':True}
            if action=='resume_rate_limited': self.service.graph.resume_rate_limited(request['run'],request['stage'],request['claim']); return {'resumed':True}
            if action=='cancel': self.service.cancel(request['run'],request['claim']); return {'cancelled':True}
            if action=='status': return {'stage':self.service.graph.get(request['run'],request['stage'])}
            if action=='artifact':
                data=self.service.artifacts.get(request['reference'])
                if len(data)>1048576: raise ControlError('artifact too large')
                return {'artifact':json.loads(data)}
            if action=='approve': self.service.approve(request['run'],request['approval'],request['version'],'local-owner'); return {'approved':True}
            if action=='complete' and self.fixture_mode: return {'result':self.service.complete(request['run'],request['stage'],request['claim'],request['evidence'])}
        except Exception: raise ControlError('request failed') from None
        raise ControlError('action unavailable')

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        try:
            data=self.rfile.readline(65537)
            if not data.endswith(b'\n') or len(data)>65536: raise ControlError('invalid request')
            request=json.loads(data)
            result=self.server.control.handle(request.get('secret'),request.get('request'))
            response={'ok':True,'result':result}
        except Exception: response={'ok':False,'error':'request rejected'}
        encoded=json.dumps(response,separators=(',',':')).encode()+b'\n'
        if len(encoded)>MAX_CONTROL_RESPONSE: encoded=b'{"ok":false,"error":"response too large"}\n'
        self.wfile.write(encoded)

class UnixControlServer(socketserver.UnixStreamServer):
    def __init__(self,path,control):
        path=Path(path); parent=path.parent
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077: raise ControlError('socket directory must be owner-only')
        if path.exists() or path.is_symlink(): raise ControlError('socket path already exists')
        self.control=control
        self.path=path
        self.bound=False
        super().__init__(str(path),_Handler)
        self.bound=True
        os.chmod(path,0o600)
    def server_close(self):
        super().server_close()
        if self.bound:
            try: self.path.unlink()
            except FileNotFoundError: pass
