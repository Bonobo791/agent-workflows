"""Release a quiescent local task's claims for an explicit worker handoff."""
import secrets
import time
from pathlib import Path

from runner.local_scope import unresolved_scope_rows
from runner.manual_lifecycle import ManualLifecycle


class CheckpointError(RuntimeError): pass


def ensure_quiescent(db,run):
    tables={value[0] for value in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'local_scopes','local_sessions','graph_attempts','external_actions','action_scopes','action_followups'}<=tables or unresolved_scope_rows(db,run): raise CheckpointError('local state unavailable or unresolved')
    checks=(('local_sessions',"SELECT 1 FROM local_sessions WHERE run=? AND state IS NOT 'idle'"),('engineering_workspaces','SELECT 1 FROM engineering_workspaces WHERE run=? AND assignment IS NOT NULL'),('graph_attempts',"SELECT 1 FROM graph_attempts WHERE run=? AND (status IS NULL OR status NOT IN ('failed','completed','not_executed'))"),('external_actions',"SELECT 1 FROM external_actions WHERE run=? AND (status IS NULL OR status NOT IN ('executed','not_executed','desired_state_observed','revoked'))"),('action_scopes','SELECT 1 FROM action_scopes WHERE run=?'),('action_followups','SELECT 1 FROM action_followups WHERE run=?'),('pr_bridge_operations',"SELECT 1 FROM pr_bridge_operations WHERE run=? AND (state IS NULL OR state NOT IN ('proposed','not_executed','executed','desired_state_observed','reconciled'))"),('supervised_commands',"SELECT 1 FROM supervised_commands WHERE run=? AND (state IS NULL OR state NOT IN ('passed','failed'))"),('correction_attempts',"SELECT 1 FROM correction_attempts WHERE run=? AND (state IS NULL OR state NOT IN ('completed','recovered'))"),('openfilm_render_jobs',"SELECT 1 FROM openfilm_render_jobs WHERE task_run=? AND state IS NOT 'exited'"))
    for table,query in checks:
        if table in tables and db.execute(query,(run,)).fetchone(): raise CheckpointError('work remains unresolved')
    if 'docker_execution_map' in tables:
        if not {'docker_assignments','docker_terminal_evidence'}<=tables or db.execute("SELECT 1 FROM docker_execution_map m LEFT JOIN docker_assignments a ON a.run=m.assignment LEFT JOIN docker_terminal_evidence e ON e.run=a.run AND e.container_id=a.container_id WHERE m.task_run=? AND (a.run IS NULL OR a.state IS NOT 'terminated' OR e.run IS NULL)",(run,)).fetchone(): raise CheckpointError('container outcome unresolved')

def checkpoint(service,run,queue_claim,graph_claim):
    lifecycle=service.lifecycle; supervisor=service.model_supervisor;db=service.graph.db
    paths=[db.execute('PRAGMA database_list').fetchone()[2]]
    if not isinstance(lifecycle,ManualLifecycle) or supervisor is None: raise CheckpointError('local manual runtime required')
    paths.extend((lifecycle.db.execute('PRAGMA database_list').fetchone()[2],getattr(supervisor,'db',None)))
    if any(not isinstance(path,str) or not path for path in paths) or len({Path(path).resolve() for path in paths})!=1: raise CheckpointError('shared durable state required')
    db.execute('CREATE TABLE IF NOT EXISTS checkpoint_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,schema_version INTEGER NOT NULL,run TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)')
    db.execute('BEGIN IMMEDIATE')
    try:
        row=db.execute("SELECT j.status,j.token,j.expires,c.token,c.expires,r.status,a.state,w.state,a.target,a.incarnation,w.incarnation,r.task_id FROM jobs j JOIN graph_runs r ON r.id=j.run JOIN graph_claims c ON c.run=r.id JOIN manual_assignments a ON a.run=r.id JOIN manual_workers w ON w.target=a.target WHERE j.run=?",(run,)).fetchone()
        now=time.time()
        if not row or row[0]!='running' or row[5:8]!=('active','running','assigned') or row[8:11]!=(lifecycle.target['manual_target'],lifecycle.incarnation,lifecycle.incarnation) or not isinstance(queue_claim,str) or not isinstance(graph_claim,str) or not isinstance(row[1],str) or not isinstance(row[3],str) or not secrets.compare_digest(queue_claim,row[1]) or not secrets.compare_digest(graph_claim,row[3]) or row[2] is None or row[4] is None or row[2]<=now or row[4]<=now: raise CheckpointError('current claims unavailable')
        ensure_quiescent(db,run)
        now=time.time()
        if row[2]<=now or row[4]<=now: raise CheckpointError('claims expired during checkpoint')
        job_audit=db.execute('SELECT COALESCE(MAX(id),0) FROM job_audit').fetchone()[0]
        manual_audit=db.execute('SELECT COALESCE(MAX(id),0) FROM manual_lifecycle_audit').fetchone()[0]
        if db.execute("INSERT INTO claim_audit(schema_version,run,task_id,status,created) VALUES(1,?,?,?,?)",(run,row[11],'released',now)).rowcount!=1: raise CheckpointError('claim audit unavailable')
        if db.execute('DELETE FROM graph_claims WHERE run=? AND token=?',(run,graph_claim)).rowcount!=1: raise CheckpointError('graph claim changed')
        if db.execute("UPDATE manual_assignments SET state='checkpointed' WHERE run=? AND state='running'",(run,)).rowcount!=1: raise CheckpointError('manual assignment changed')
        if db.execute("UPDATE manual_workers SET state='checkpointed' WHERE target=? AND incarnation=? AND state='assigned'",(lifecycle.target['manual_target'],lifecycle.incarnation)).rowcount!=1: raise CheckpointError('manual worker changed')
        if db.execute("UPDATE jobs SET status='queued',worker=NULL,token=NULL,expires=NULL WHERE run=? AND status='running'",(run,)).rowcount!=1: raise CheckpointError('job claim changed')
        if not db.execute("SELECT 1 FROM job_audit WHERE id>? AND run=? AND status='queued'",(job_audit,run)).fetchone() or not db.execute("SELECT 1 FROM manual_lifecycle_audit WHERE id>? AND run=? AND state='checkpointed'",(manual_audit,run)).fetchone(): raise CheckpointError('handoff audit unavailable')
        if db.execute("INSERT INTO checkpoint_audit(schema_version,run,status,created) VALUES(1,?,'checkpointed',?)",(run,now)).rowcount!=1: raise CheckpointError('checkpoint audit unavailable')
        commit_time=time.time()
        if row[2]<=commit_time or row[4]<=commit_time: raise CheckpointError('claims expired during checkpoint')
        db.execute('COMMIT');return {'run':run,'state':'checkpointed'}
    except Exception:
        db.execute('ROLLBACK');raise
