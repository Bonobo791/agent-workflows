"""Explicit, private configuration for the local managed-auth worker."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from runner.adapter import managed_app_server_spec
from runner.local_scope import SystemdScopes
from runner.local_supervisor import LocalSupervisor

class LocalConfigError(RuntimeError): pass

def local_supervisor_factory(config,environment,root):
    if not isinstance(config,dict) or set(config)!={'codex_executable','codex_sha256','disabled_integrations'} or not isinstance(config['codex_sha256'],str) or not re.fullmatch(r'[0-9a-f]{64}',config['codex_sha256']): raise LocalConfigError('invalid local runtime configuration')
    if not isinstance(environment,dict) and not hasattr(environment,'items'): raise LocalConfigError('invalid local environment')
    if not isinstance(config['disabled_integrations'],dict): raise LocalConfigError('explicit disabled integrations required')
    env={k:environment[k] for k in ('HOME','CODEX_HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if k in environment}
    try:
        args,child_env=managed_app_server_spec(config['codex_executable'],env,disabled_integrations=config['disabled_integrations'])
        binary=Path(args[0]); state=Path(root)
        if not state.is_absolute(): raise ValueError()
        state.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=state.lstat()
        if state.resolve(strict=True)!=state or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077: raise ValueError()
    except Exception: raise LocalConfigError('invalid local executable or state directory') from None
    expected=config['codex_sha256']; integrations={key:list(value) for key,value in config['disabled_integrations'].items()}
    policy_hash=hashlib.sha256(json.dumps({'codex_executable':str(binary),'codex_sha256':expected,'disabled_integrations':integrations,'state':str(state),'sandbox_settings':args[4:],'environment':child_env,'engineering_git_write':'implement-only'},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def verify_policy():
        try:
            fd=os.open(binary,os.O_RDONLY|os.O_NOFOLLOW)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_mode&0o022: return False
                with os.fdopen(fd,'rb',closefd=False) as stream:
                    return hashlib.file_digest(stream,'sha256').hexdigest()==expected
            finally: os.close(fd)
        except OSError: return False
    if not verify_policy(): raise LocalConfigError('pinned Codex executable mismatch')
    def factory(service):
        manager={k:env[k] for k in ('HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if k in env};manager['PATH']='/usr/bin:/bin'
        def workspace_for(run,role):
            if not all(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',value) for value in (run,role)): raise LocalConfigError('invalid workspace identity')
            db=sqlite3.connect(str(state/'state.db'))
            try: row=db.execute('SELECT workflow,definition_hash FROM graph_runs WHERE id=?',(run,)).fetchone()
            finally:db.close()
            if not row or row[0] not in service.graph.definitions or row[1]!=service.graph.definitions[row[0]].fingerprint(): raise LocalConfigError('run or workflow unavailable')
            workflow=row[0]
            if workflow=='engineering' and role in ('implement','test','qa'):
                from runner.engineering_workspace import EngineeringWorkspace
                try:
                    registry=EngineeringWorkspace(state)
                    try:
                        row=registry.db.execute('SELECT repository,base,branch,owner,path,policy,assignment FROM engineering_workspaces WHERE run=?',(run,)).fetchone()
                        task=service.task_intake.config
                        if not row or row[0]!=task['github_owner']+'/'+task['github_repo'] or row[2]!=task['github_branch'] or row[5]!=policy_hash or row[6] is not None or registry.verify(run,*row[:4],row[5])!=row[4]: raise ValueError()
                        return row[4]
                    finally:registry.close()
                except Exception: raise LocalConfigError('verified engineering workspace unavailable') from None
            parent=state
            try:
                for name in ('local-workspaces',run,role):
                    parent=parent/name; parent.mkdir(mode=0o700,exist_ok=True); info=parent.lstat()
                    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or parent.resolve(strict=True)!=parent: raise ValueError()
            except (OSError,ValueError): raise LocalConfigError('private workspace unavailable') from None
            return str(parent)
        return LocalSupervisor(str(state/'state.db'),str(binary),env,SystemdScopes(manager),lambda _:False,workspace_for,verify_policy,integrations,engineering_root=str(state),policy_hash=policy_hash)
    return factory
