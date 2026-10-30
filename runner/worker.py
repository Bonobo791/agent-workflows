"""Private Unix-socket control worker; live execution remains disabled."""
import os
import signal
import stat
import sys
from pathlib import Path
from runner.control import ControlPlane, UnixControlServer
from runner.service import Service

class ConfigurationError(RuntimeError): pass

def build_control(root, secret, *, execution_mode='disabled', lifecycle_factory=None, intake_factory=None, docker_socket=None, docker_image=None, docker_config=None, task_config=None, plan_token=None, github_token=None, role_config=None, command_config=None, pr_credentials=None):
    """Construct the private control plane; trusted integrations may inject lifecycle.

    Injection is deliberately a Python integration boundary, not an environment
    variable naming arbitrary code. The distributed entry point supplies none.
    """
    if execution_mode != 'disabled':
        raise ConfigurationError('live execution requires approved credential delivery, verified worker isolation, and workflow dispatch')
    if task_config is not None and (intake_factory is not None or not plan_token or not github_token): raise ConfigurationError('configured task requires private Plan and GitHub credentials')
    if docker_config is not None and (docker_socket or docker_image): raise ConfigurationError('choose one Docker configuration')
    if bool(docker_socket) != bool(docker_image): raise ConfigurationError('Docker socket and immutable image required together')
    service=Service(root)
    service.state_db_path=str(Path(root)/'state.db')
    try:
        if role_config is not None:
            from runner.skills import SkillResolver
            if not isinstance(role_config,dict) or set(role_config)!={'skill_roots','required_skills'} or not isinstance(role_config['skill_roots'],list) or not role_config['skill_roots'] or any(not isinstance(x,str) or not Path(x).is_absolute() for x in role_config['skill_roots']) or not isinstance(role_config['required_skills'],dict): raise ConfigurationError('invalid role skill configuration')
            stages=set().union(*(definition.dependencies for definition in service.graph.definitions.values())) | {'source_review'}
            if any(stage not in stages or not isinstance(names,list) or not names or any(not isinstance(name,str) or not name for name in names) or len(set(names))!=len(names) for stage,names in role_config['required_skills'].items()): raise ConfigurationError('invalid role skill configuration')
            service.skill_resolver=SkillResolver(role_config['skill_roots'])
            service.required_skills={stage:list(names) for stage,names in role_config['required_skills'].items()}
        if docker_config is not None:
            from runner.docker_config import DockerConfig
            from runner.docker_controller import DockerAssignments, UnixEngine
            from runner.docker_supervisor import DockerSupervisor
            from runner.codex_executor import CodexExecutor
            from runner.session_store import SessionStore
            from runner.model_policy import ModelPolicy
            from runner.adapter import CredentialDelivery
            config=DockerConfig(docker_config,Path(root))
            service.docker_assignments=DockerAssignments(str(Path(root)/'state.db'),UnixEngine(config.socket),config.image)
            service.docker_supervisor=DockerSupervisor(service.docker_assignments,config,lambda run: manual_fence_ready(service,run),session_root=Path(root),credential_delivery=CredentialDelivery())
            service.model_policy=ModelPolicy()
            service.docker_executor=CodexExecutor(service.docker_supervisor,service.provenance,service.artifacts,service.fence_execution,model_policy=service.model_policy)
            service.session_store=SessionStore(Path(root))
        if docker_socket:
            from runner.docker_controller import DockerAssignments, UnixEngine
            service.docker_assignments=DockerAssignments(str(Path(root)/'state.db'),UnixEngine(docker_socket),docker_image)
            from runner.docker_supervisor import DockerSupervisor
            service.docker_supervisor=DockerSupervisor(service.docker_assignments)
        if lifecycle_factory is not None:
            service.lifecycle=lifecycle_factory(service)
            if service.lifecycle is None: raise ConfigurationError('lifecycle provider unavailable')
        if task_config is not None:
            from runner.configured_task import ConfiguredTask
            from runner.plan_mcp import HTTPSMCP, PlanMCP
            from workflows.engineering.github import HTTPSGitHub
            service.task_intake=ConfiguredTask(service,task_config,PlanMCP(HTTPSMCP(task_config.get('plan_endpoint',''),plan_token)),HTTPSGitHub(github_token))
        if intake_factory is not None:
            service.task_intake=intake_factory(service)
            if service.task_intake is None: raise ConfigurationError('task intake unavailable')
        if pr_credentials is not None:
            if not isinstance(pr_credentials,dict) or set(pr_credentials)!={'push','mutation'} or service.docker_supervisor is None or service.docker_supervisor.config is None or service.task_intake is None or not callable(getattr(getattr(service.task_intake,'github',None),'request',None)): raise ConfigurationError('PR authority requires trusted credential issuers, task client, and Docker configuration')
            from runner.pr_authority import PRRuntimeAuthority
            service.pr_authority=PRRuntimeAuthority(service,service.task_intake.github,pr_credentials['push'],pr_credentials['mutation'])
        from runner.pr_bridge import PRBridge
        service.pr_bridge=PRBridge(service)
        from runner.human_review import HumanReview
        service.human_review=HumanReview(service)
        from runner.command_supervisor import CommandSupervisor
        if command_config is not None:
            import hashlib, json
            if service.docker_supervisor is None or service.docker_supervisor.config is None: raise ConfigurationError('Docker runtime required for command supervisor')
            from runner.docker_command import DockerCommandBackend
            digest=hashlib.sha256(json.dumps(command_config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            service.command_supervisor=CommandSupervisor(service,command_config,digest,DockerCommandBackend(service.docker_supervisor))
        else: service.command_supervisor=CommandSupervisor(service)
        from runner.source_reviewer import SourceReviewer
        service.source_reviewer=SourceReviewer(service)
        from runner.stage_dispatch import StageDispatcher
        service.stage_dispatcher=StageDispatcher(service)
        return service,ControlPlane(service,secret)
    except Exception:
        service.close()
        raise

def read_secret(path):
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077 or info.st_size>4096: raise ConfigurationError('unsafe control secret')
            with os.fdopen(fd,'r',encoding='utf-8',closefd=False) as f: secret=f.read().strip()
        finally: os.close(fd)
    except (OSError, UnicodeError): raise ConfigurationError('control secret unavailable') from None
    if len(secret)<32: raise ConfigurationError('control secret too short')
    return secret

def manual_fence_ready(service,run):
    import sqlite3, time
    from runner.manual_lifecycle import ManualLifecycle
    lifecycle=service.lifecycle
    if not isinstance(lifecycle,ManualLifecycle): return False
    if run is None: return True
    db=sqlite3.connect(service.state_db_path)
    try:
        row=db.execute("SELECT a.state,w.state,j.status FROM manual_assignments a JOIN manual_workers w ON w.target=a.target AND w.incarnation=a.incarnation JOIN jobs j ON j.run=a.run WHERE a.run=? AND a.target=? AND a.incarnation=? AND j.expires>?",(run,lifecycle.target['manual_target'],lifecycle.incarnation,time.time())).fetchone()
        return row==('running','assigned','running')
    finally: db.close()

def read_runtime_config(path):
    import json
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077 or info.st_size>16384: raise ConfigurationError('unsafe runtime configuration')
            with os.fdopen(fd,'r',encoding='utf-8',closefd=False) as stream: data=json.load(stream)
        finally: os.close(fd)
        if not isinstance(data,dict): raise ConfigurationError('invalid runtime configuration')
        return data
    except (OSError,ValueError,UnicodeError): raise ConfigurationError('runtime configuration unavailable') from None

def main():
    path=os.environ.get('CONTROL_SECRET_FILE')
    if not path: raise ConfigurationError('CONTROL_SECRET_FILE required')
    root=Path(os.environ.get('WORKFLOW_DATA_DIR','/data'))
    secret=read_secret(path)
    mode=os.environ.get('WORKFLOW_LIFECYCLE_MODE','disabled')
    if mode not in ('disabled','manual'): raise ConfigurationError('unsupported lifecycle mode')
    factory=None
    if mode=='manual':
        from runner.manual_lifecycle import ManualLifecycle
        target=os.environ.get('MANUAL_WORKER_TARGET'); incarnation=os.environ.get('MANUAL_WORKER_INCARNATION')
        if not target or not incarnation: raise ConfigurationError('manual worker target and incarnation required')
        factory=lambda service: ManualLifecycle(str(root/'state.db'),target,incarnation)
    config_path=os.environ.get('DOCKER_RUNTIME_CONFIG_FILE')
    config=read_runtime_config(config_path) if config_path else None
    task_path=os.environ.get('WORKFLOW_TASK_CONFIG_FILE')
    task_config=read_runtime_config(task_path) if task_path else None
    role_path=os.environ.get('WORKFLOW_ROLE_CONFIG_FILE')
    role_config=read_runtime_config(role_path) if role_path else None
    command_path=os.environ.get('WORKFLOW_COMMAND_CONFIG_FILE')
    command_config=read_runtime_config(command_path) if command_path else None
    plan_token=read_secret(os.environ['PLAN_TOKEN_FILE']) if task_config is not None and os.environ.get('PLAN_TOKEN_FILE') else None
    github_token=read_secret(os.environ['GITHUB_TOKEN_FILE']) if task_config is not None and os.environ.get('GITHUB_TOKEN_FILE') else None
    service,control=build_control(root,secret,docker_config=config,role_config=role_config,command_config=command_config,task_config=task_config,plan_token=plan_token,github_token=github_token,execution_mode=os.environ.get('WORKFLOW_EXECUTION_MODE','disabled'),lifecycle_factory=factory,docker_socket=os.environ.get('DOCKER_CONTROLLER_SOCKET'),docker_image=os.environ.get('DOCKER_WORKER_IMAGE'))
    from runner.auto_worker import AutoWorker
    def auto_factory():
        return build_control(root,secret,docker_config=config,role_config=role_config,command_config=command_config,task_config=task_config,plan_token=plan_token,github_token=github_token,execution_mode=os.environ.get('WORKFLOW_EXECUTION_MODE','disabled'),lifecycle_factory=factory,docker_socket=os.environ.get('DOCKER_CONTROLLER_SOCKET'),docker_image=os.environ.get('DOCKER_WORKER_IMAGE'))
    control.auto_worker=AutoWorker(auto_factory,root) if task_config is not None and mode=='manual' and config is not None and role_config is not None else None
    try:
        server=UnixControlServer(root/'control.sock',control)
        try:
            server.timeout=1
            stopping=[False]
            def stop(*_): stopping[0]=True
            signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
            print('Private control socket active; live execution disabled.',flush=True)
            while not stopping[0]: server.handle_request()
        finally: server.server_close()
    finally:
        if control.auto_worker is not None: control.auto_worker.stop()
        if service.lifecycle is not None and hasattr(service.lifecycle,'close'): service.lifecycle.close()
        service.close()

if __name__=='__main__':
    try: main()
    except (ConfigurationError, OSError, RuntimeError) as exc:
        print('Worker configuration error: '+str(exc),file=sys.stderr)
        sys.exit(1)
