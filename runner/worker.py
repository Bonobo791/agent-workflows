"""Private Unix-socket control worker; live execution remains disabled."""
import os
import signal
import stat
import sys
from pathlib import Path
from runner.control import ControlPlane, UnixControlServer
from runner.service import Service

REQUIRED_SEO_SKILL='plan-seo-geo-growth'
OPENFILM_SKILL='openfilm'

def _attach_openfilm_skill(service,package_root,state_root):
    # The OpenFilm manual is the asset_production skill: Codex writes film.html from it, and the render checks it.
    from runner.skills import SkillError, SkillResolver
    from runner.trace import trace
    try: manual=(Path(package_root).resolve()/'MANUAL.md').read_text(encoding='utf-8')
    except (OSError,UnicodeError) as exc: raise ConfigurationError('OpenFilm manual unavailable') from exc
    skills_root=Path(state_root).resolve()/'openfilm-skill'
    target=skills_root/OPENFILM_SKILL/'SKILL.md'
    body='---\nname: openfilm\ndescription: Write OpenFilm films (film.html, pages and clips) from the supplied brief.\n---\n\n'+manual
    if not target.is_file() or target.read_text(encoding='utf-8')!=body:
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temporary=target.with_suffix('.tmp'); temporary.write_text(body,encoding='utf-8'); os.replace(temporary,target)
    resolver=service.skill_resolver
    try:
        if resolver is None: raise SkillError('no skill resolver')
        resolver.resolve([OPENFILM_SKILL])  # an operator's own openfilm skill stands; two copies would be ambiguous
        source='operator'
    except SkillError:
        source='manual'
        service.skill_resolver=SkillResolver(([str(root) for root in resolver.roots] if resolver is not None else [])+[str(skills_root)])
    names=service.required_skills.setdefault('asset_production',[])
    if OPENFILM_SKILL not in names: names.append(OPENFILM_SKILL)
    trace('worker.openfilm_skill', source=source, names=names, bytes=len(body.encode('utf-8')))

class ConfigurationError(RuntimeError): pass

def build_control(root, secret, *, openfilm_package_root=None, execution_mode='disabled', lifecycle_factory=None, intake_factory=None, task_config=None, plan_token=None, github_token=None, role_config=None, command_config=None, openfilm_builder_factory=None, reddit_context_factory=None, page_rules=None, local_supervisor_factory=None, github_http=None, source_config=None, platform_config=None, audio_config=None):
    """Construct the private control plane; trusted integrations may inject lifecycle.

    Injection is deliberately a Python integration boundary, not an environment
    variable naming arbitrary code. The distributed entry point supplies none.
    """
    if execution_mode not in ('disabled','local'):
        raise ConfigurationError('live execution requires approved credential delivery, verified worker isolation, and workflow dispatch')
    if execution_mode=='local' and (not callable(local_supervisor_factory) or lifecycle_factory is None): raise ConfigurationError('local execution requires a trusted supervisor and manual lifecycle')
    if execution_mode!='local' and local_supervisor_factory is not None: raise ConfigurationError('local supervisor requires local execution mode')
    if github_http is not None and (execution_mode!='local' or github_token is not None or not callable(getattr(github_http,'request',None))): raise ConfigurationError('invalid local GitHub read client')
    plan_gateway=isinstance(task_config,dict) and task_config.get('plan_endpoint')=='codex-apps'
    if plan_gateway and (execution_mode!='local' or plan_token is not None): raise ConfigurationError('Codex tracker gateway requires local execution without a token')
    if task_config is not None and (intake_factory is not None or (not plan_gateway and not plan_token) or (not github_token and github_http is None)): raise ConfigurationError('configured task requires Plan and GitHub read access')
    if source_config is not None and (execution_mode!='local' or not isinstance(source_config,dict) or set(source_config)!={'urls'}): raise ConfigurationError('local source URL policy required')
    if platform_config is not None and execution_mode!='local': raise ConfigurationError('local platform policy required')
    if audio_config is not None and execution_mode!='local': raise ConfigurationError('local audio policy required')
    service=Service(root)
    service.state_db_path=str(Path(root)/'state.db')
    try:
        if page_rules is not None:
            from runner.page_structure import policy_bytes
            if not isinstance(page_rules,dict) or not page_rules: raise ConfigurationError('page rules unavailable')
            try:
                for format_name in page_rules: policy_bytes(page_rules,format_name)
            except Exception: raise ConfigurationError('invalid page rules')
            import copy
            service.page_rules=copy.deepcopy(page_rules)
        if role_config is not None:
            from runner.skills import SkillResolver
            if not isinstance(role_config,dict) or set(role_config)!={'skill_roots','required_skills'} or not isinstance(role_config['skill_roots'],list) or not role_config['skill_roots'] or any(not isinstance(x,str) or not Path(x).is_absolute() for x in role_config['skill_roots']) or not isinstance(role_config['required_skills'],dict): raise ConfigurationError('invalid role skill configuration')
            stages=set().union(*(definition.dependencies for definition in service.graph.definitions.values())) | {'source_review','asset_review','platform_review'}
            if any(stage not in stages or not isinstance(names,list) or not names or any(not isinstance(name,str) or not name for name in names) or len(set(names))!=len(names) for stage,names in role_config['required_skills'].items()): raise ConfigurationError('invalid role skill configuration')
            service.skill_resolver=SkillResolver(role_config['skill_roots'])
            service.required_skills={stage:list(names) for stage,names in role_config['required_skills'].items()}
            for stage in ('canonical_draft','canonical_review'):
                if stage in stages and REQUIRED_SEO_SKILL not in service.required_skills.setdefault(stage,[]): service.required_skills[stage].append(REQUIRED_SEO_SKILL)
        if lifecycle_factory is not None:
            service.lifecycle=lifecycle_factory(service)
            if service.lifecycle is None: raise ConfigurationError('lifecycle provider unavailable')
        if execution_mode=='local':
            from runner.manual_lifecycle import ManualLifecycle
            from runner.local_supervisor import LocalSupervisor
            from runner.codex_executor import CodexExecutor
            from runner.model_policy import ModelPolicy
            if not isinstance(service.lifecycle,ManualLifecycle): raise ConfigurationError('local execution requires manual lifecycle')
            supervisor=local_supervisor_factory(service)
            if not isinstance(supervisor,LocalSupervisor) or Path(supervisor.db).resolve()!=Path(service.state_db_path).resolve(): raise ConfigurationError('local supervisor state mismatch')
            supervisor.fence=lambda run: manual_fence_ready(service,run)
            service.model_supervisor=supervisor
            service.model_policy=ModelPolicy()
            service.model_executor=CodexExecutor(supervisor,service.provenance,service.artifacts,service.fence_execution,model_policy=service.model_policy)
        if reddit_context_factory is not None:
            if not callable(reddit_context_factory): raise ConfigurationError('Reddit context authority unavailable')
            service.reddit_context_authority=reddit_context_factory(service)
            if not all(callable(getattr(service.reddit_context_authority,name,None)) for name in ('check','verify')): raise ConfigurationError('Reddit context authority unavailable')
        if source_config is not None:
            from runner.source_fetch import SourceFetcher
            from runner.source_snapshots import SourceSnapshots
            service.source_snapshots=SourceSnapshots(service,SourceFetcher(source_config['urls']))
        if platform_config is not None:
            from runner.platform_reviewer import PlatformReviewer
            service.openfilm_platform_authority=PlatformReviewer(service,platform_config)
        if audio_config is not None:
            from runner.audio_transcriber import AudioTranscriber
            service.audio_transcriber=AudioTranscriber(service,audio_config)
        if execution_mode=='local' and service.required_skills.get('asset_review'):
            from runner.local_asset_review import LocalAssetReviewer
            service.openfilm_reviewer=LocalAssetReviewer(service)
        package_root=openfilm_package_root or os.environ.get('OPENFILM_PACKAGE_ROOT')
        if execution_mode=='local' and package_root and service.model_supervisor is not None:
            _attach_openfilm_skill(service,package_root,root)
            from runner.openfilm_builder import CodexOpenFilmProjectBuilder
            from runner.openfilm_local import LocalOpenFilmBackend
            builder=openfilm_builder_factory(service) if openfilm_builder_factory is not None else CodexOpenFilmProjectBuilder(service)
            renders=os.path.join(root,'openfilm-renders'); os.makedirs(renders,mode=0o700,exist_ok=True)
            service.openfilm_backend=LocalOpenFilmBackend(service,builder,package_root,renders)
        if task_config is not None:
            from runner.configured_task import ConfiguredTask
            from runner.plan_mcp import HTTPSMCP, PlanMCP
            from workflows.engineering.github import HTTPSGitHub
            if plan_gateway:
                from runner.plan_codex import CodexAppsPlan
                supervisor=service.model_supervisor
                plan=CodexAppsPlan(supervisor.executable,supervisor.environment,supervisor.verify_policy,str(Path(root).resolve()))
            else: plan=PlanMCP(HTTPSMCP(task_config.get('plan_endpoint',''),plan_token))
            service.task_intake=ConfiguredTask(service,task_config,plan,github_http if github_http is not None else HTTPSGitHub(github_token))
        if intake_factory is not None:
            service.task_intake=intake_factory(service)
            if service.task_intake is None: raise ConfigurationError('task intake unavailable')
        if execution_mode=='local' and github_http is not None and service.task_intake is not None:
            from runner.gh_read import GHRead
            if isinstance(github_http,GHRead):
                from runner.local_pr_authority import LocalPRAuthority
                service.pr_authority=LocalPRAuthority(service,github_http)
        from runner.pr_bridge import PRBridge
        service.pr_bridge=PRBridge(service)
        from runner.human_review import HumanReview
        service.human_review=HumanReview(service)
        from runner.command_supervisor import CommandSupervisor
        if command_config is not None:
            import hashlib, json
            digest=hashlib.sha256(json.dumps(command_config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if service.model_supervisor is None: raise ConfigurationError('local runtime required for command supervisor')
            from runner.local_command import LocalCommandBackend
            backend=LocalCommandBackend(service.model_supervisor)
            service.command_supervisor=CommandSupervisor(service,command_config,digest,backend)
        else: service.command_supervisor=CommandSupervisor(service)
        from runner.source_reviewer import SourceReviewer
        service.source_reviewer=SourceReviewer(service)
        from runner.stage_dispatch import StageDispatcher
        service.stage_dispatcher=StageDispatcher(service)
        return service,ControlPlane(service,secret)
    except Exception:
        if service.lifecycle is not None and callable(getattr(service.lifecycle,'close',None)): service.lifecycle.close()
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
    except (OSError, UnicodeError): raise ConfigurationError('control secret unavailable')
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
    except (OSError,ValueError,UnicodeError): raise ConfigurationError('runtime configuration unavailable')

def lock_state_directory(root):
    import fcntl
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(directory)
        if info.st_uid!=os.geteuid() or info.st_mode&0o077: raise ConfigurationError('state directory must be owner-only')
        fd=os.open('.worker.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_nlink!=1: raise ConfigurationError('unsafe worker lock')
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise ConfigurationError('state directory already in use')
            return fd
        except Exception:
            os.close(fd); raise
    finally: os.close(directory)

def main():
    path=os.environ.get('CONTROL_SECRET_FILE')
    if not path: raise ConfigurationError('CONTROL_SECRET_FILE required')
    root=Path(os.environ.get('WORKFLOW_DATA_DIR','/data'))
    secret=read_secret(path)
    lock=lock_state_directory(root)
    try:
        configured=os.environ.get('WORKFLOW_CONTROL_DIR')
        legacy=root/'control.sock'
        if configured and Path(configured)!=root and (legacy.exists() or legacy.is_symlink()):
            raise ConfigurationError('existing state-directory socket requires inspection')
        return run_worker(root,secret)
    finally: os.close(lock)

def run_worker(root,secret):
    mode=os.environ.get('WORKFLOW_LIFECYCLE_MODE','disabled')
    if mode not in ('disabled','manual'): raise ConfigurationError('unsupported lifecycle mode')
    factory=None
    if mode=='manual':
        from runner.manual_lifecycle import ManualLifecycle
        target=os.environ.get('MANUAL_WORKER_TARGET'); incarnation=os.environ.get('MANUAL_WORKER_INCARNATION')
        if not target or not incarnation: raise ConfigurationError('manual worker target and incarnation required')
        factory=lambda service: ManualLifecycle(str(root/'state.db'),target,incarnation)
    task_path=os.environ.get('WORKFLOW_TASK_CONFIG_FILE')
    task_config=read_runtime_config(task_path) if task_path else None
    role_path=os.environ.get('WORKFLOW_ROLE_CONFIG_FILE')
    role_config=read_runtime_config(role_path) if role_path else None
    command_path=os.environ.get('WORKFLOW_COMMAND_CONFIG_FILE')
    command_config=read_runtime_config(command_path) if command_path else None
    page_path=os.environ.get('WORKFLOW_PAGE_RULE_CONFIG_FILE')
    page_rules=read_runtime_config(page_path) if page_path else None
    source_path=os.environ.get('WORKFLOW_SOURCE_CONFIG_FILE')
    source_config=read_runtime_config(source_path) if source_path else None
    platform_path=os.environ.get('WORKFLOW_PLATFORM_CONFIG_FILE')
    platform_config=read_runtime_config(platform_path) if platform_path else None
    audio_path=os.environ.get('WORKFLOW_AUDIO_CONFIG_FILE')
    audio_config=read_runtime_config(audio_path) if audio_path else None
    execution_mode=os.environ.get('WORKFLOW_EXECUTION_MODE','disabled')
    local_factory=None
    if execution_mode=='local':
        local_path=os.environ.get('LOCAL_RUNTIME_CONFIG_FILE')
        if not local_path: raise ConfigurationError('LOCAL_RUNTIME_CONFIG_FILE required')
        from runner.local_config import local_supervisor_factory
        local_factory=local_supervisor_factory(read_runtime_config(local_path),os.environ,root)
    if isinstance(task_config,dict) and task_config.get('plan_endpoint')=='codex-apps' and os.environ.get('PLAN_TOKEN_FILE'): raise ConfigurationError('Codex tracker gateway must not use a Plan token')
    plan_token=read_secret(os.environ['PLAN_TOKEN_FILE']) if task_config is not None and os.environ.get('PLAN_TOKEN_FILE') else None
    github_token=read_secret(os.environ['GITHUB_TOKEN_FILE']) if task_config is not None and os.environ.get('GITHUB_TOKEN_FILE') else None
    github_http=None
    if execution_mode=='local' and task_config is not None and github_token is None:
        import shutil
        from runner.gh_read import GHRead
        executable=shutil.which('gh')
        if not executable: raise ConfigurationError('GitHub CLI unavailable')
        github_http=GHRead(executable,os.environ)
    service,control=build_control(root,secret,role_config=role_config,command_config=command_config,page_rules=page_rules,task_config=task_config,plan_token=plan_token,github_token=github_token,execution_mode=execution_mode,lifecycle_factory=factory,local_supervisor_factory=local_factory,github_http=github_http,source_config=source_config,platform_config=platform_config,audio_config=audio_config)
    from runner.auto_worker import AutoWorker
    def auto_factory():
        return build_control(root,secret,role_config=role_config,command_config=command_config,page_rules=page_rules,task_config=task_config,plan_token=plan_token,github_token=github_token,execution_mode=execution_mode,lifecycle_factory=factory,local_supervisor_factory=local_factory,github_http=github_http,source_config=source_config,platform_config=platform_config,audio_config=audio_config)
    control.auto_worker=AutoWorker(auto_factory,root) if task_config is not None and mode=='manual' and local_factory is not None and role_config is not None else None
    try:
        socket_dir=Path(os.environ.get('WORKFLOW_CONTROL_DIR') or root)
        if os.environ.get('WORKFLOW_CONTROL_DIR'): socket_dir.mkdir(mode=0o700,exist_ok=True)
        server=UnixControlServer(socket_dir/'control.sock',control)
        try:
            server.timeout=1
            stopping=[False]
            def stop(*_): stopping[0]=True
            signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
            print('Private control socket active; execution mode: '+execution_mode+'.',flush=True)
            while not stopping[0]: server.handle_request()
        finally: server.server_close()
    finally:
        try:
            if control.auto_worker is not None: control.auto_worker.stop(service)
        finally:
            if service.lifecycle is not None and hasattr(service.lifecycle,'close'): service.lifecycle.close()
            service.close()

if __name__=='__main__':
    try: main()
    except (ConfigurationError, OSError, RuntimeError) as exc:
        print('Worker configuration error: '+str(exc),file=sys.stderr)
        sys.exit(1)
