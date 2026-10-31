"""Private, on-demand aggregate metrics from authoritative SQLite state."""
import math
import time
from runner.audit_export import STAGES, STATUSES, ROLES, TOOL_KINDS

WORKFLOWS=frozenset(('engineering','marketing'))


def snapshot(db, now=None):
    """Return a consistent, bounded-label snapshot; never mutate workflow state."""
    now=time.time() if now is None else now
    if not isinstance(now,(int,float)) or isinstance(now,bool) or not math.isfinite(now) or now<0 or db.in_transaction: raise ValueError('invalid metric snapshot')
    db.execute('BEGIN')
    try:
        jobs=db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status').fetchall()
        expired=db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('running','cancelling') AND expires IS NOT NULL AND expires<=?",(now,)).fetchone()[0]
        stages=db.execute('SELECT r.workflow,s.stage,s.status,COUNT(*) FROM graph_stages s JOIN graph_runs r ON r.id=s.run GROUP BY r.workflow,s.stage,s.status').fetchall()
        attempts=db.execute('SELECT r.workflow,a.stage,a.status,COUNT(*),SUM(CASE WHEN a.count>1 THEN 1 ELSE 0 END) FROM graph_attempts a JOIN graph_runs r ON r.id=a.run GROUP BY r.workflow,a.stage,a.status').fetchall()
        cache=db.execute("SELECT r.workflow,a.stage,COUNT(*) FROM cache_audit a JOIN graph_runs r ON r.id=a.run WHERE a.status='reused' GROUP BY r.workflow,a.stage").fetchall()
        launches=db.execute('SELECT r.workflow,a.role,a.status,COUNT(*) FROM execution_launch_audit a JOIN (SELECT MAX(id) id FROM execution_launch_audit GROUP BY run,execution) latest ON latest.id=a.id JOIN graph_runs r ON r.id=a.run GROUP BY r.workflow,a.role,a.status').fetchall()
        tools=db.execute('SELECT r.workflow,a.role,a.tool_kind,a.status,COUNT(*) FROM tool_audit a JOIN graph_runs r ON r.id=a.run GROUP BY r.workflow,a.role,a.tool_kind,a.status').fetchall()
        approvals=db.execute('SELECT status,COUNT(*) FROM external_actions GROUP BY status').fetchall()
        pauses=db.execute('SELECT r.workflow,COUNT(*) FROM quota_pauses p JOIN graph_runs r ON r.id=p.run GROUP BY r.workflow').fetchall()
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK'); raise
    result={}
    def add(metric, labels, value):
        key=(metric,labels); result[key]=result.get(key,0)+value
    def status(value): return value if isinstance(value,str) and value in STATUSES else 'unavailable'
    def workflow(value): return value if isinstance(value,str) and value in WORKFLOWS else 'unavailable'
    def stage(value): return value if isinstance(value,str) and value in STAGES else 'unavailable'
    def role(value): return value if isinstance(value,str) and value in ROLES else 'unavailable'
    def tool(value): return value if isinstance(value,str) and value in TOOL_KINDS else 'unavailable'
    for state,count in jobs: add('workflow_jobs',(('status',status(state)),),count)
    add('workflow_snapshot_timestamp_seconds',(),int(now))
    add('workflow_expired_leases',(),expired)
    for flow,name,state,count in stages: add('workflow_stages',(('workflow',workflow(flow)),('stage',stage(name)),('status',status(state))),count)
    for flow,name,state,count,retried in attempts:
        labels=(('workflow',workflow(flow)),('stage',stage(name)),('status',status(state)))
        add('workflow_attempts',labels,count); add('workflow_retried_inputs',labels,retried)
    for flow,name,count in cache: add('workflow_cache_reuses',(('workflow',workflow(flow)),('stage',stage(name))),count)
    for flow,name,state,count in launches: add('workflow_launch_observations',(('workflow',workflow(flow)),('role',role(name)),('status',status(state))),count)
    for flow,name,kind,state,count in tools: add('workflow_observed_tools',(('workflow',workflow(flow)),('role',role(name)),('tool',tool(kind)),('status',status(state))),count)
    for state,count in approvals: add('workflow_actions',(('status',status(state)),),count)
    for flow,count in pauses: add('workflow_quota_pauses',(('workflow',workflow(flow)),),count)
    return result


def prometheus_text(metrics):
    """Encode only metrics created by snapshot, without accepting arbitrary labels."""
    if not isinstance(metrics,dict): raise ValueError('invalid metrics')
    allowed={'workflow_jobs':('status',),'workflow_snapshot_timestamp_seconds':(),'workflow_expired_leases':(),'workflow_stages':('workflow','stage','status'),'workflow_attempts':('workflow','stage','status'),'workflow_retried_inputs':('workflow','stage','status'),'workflow_cache_reuses':('workflow','stage'),'workflow_launch_observations':('workflow','role','status'),'workflow_observed_tools':('workflow','role','tool','status'),'workflow_actions':('status',),'workflow_quota_pauses':('workflow',)}
    domains={'workflow':WORKFLOWS|{'unavailable'},'stage':STAGES|{'unavailable'},'role':ROLES|{'unavailable'},'tool':TOOL_KINDS|{'unavailable'},'status':STATUSES|{'unavailable'}}
    lines=[]
    for (name,labels),value in sorted(metrics.items()):
        if name not in allowed or not isinstance(labels,tuple) or tuple(key for key,_ in labels)!=allowed[name] or any(val not in domains[key] for key,val in labels) or type(value) is not int or value<0: raise ValueError('invalid metric sample')
        encoded='{'+','.join(f'{key}="{val}"' for key,val in labels)+'}' if labels else ''
        lines.append(f'{name}{encoded} {value}')
    return '\n'.join(lines)+'\n'
