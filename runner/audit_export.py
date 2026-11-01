"""Privacy-preserving projection of private audit records for telemetry."""
import hashlib
import hmac
import math
import os
import json

from runner.graph import engineering_definition, marketing_definition

STAGES = frozenset(engineering_definition().dependencies) | frozenset(marketing_definition().dependencies)
ROLES = STAGES | frozenset(('source_review','asset_review','platform_review'))
TOOL_KINDS = frozenset(('mcpToolCall','webSearch','commandExecution'))
STATUSES = frozenset(('passed', 'blocked', 'failed', 'skipped', 'untested', 'unsuitable', 'awaiting_review', 'awaiting_approval', 'rate_limited', 'invalidated', 'resumed', 'reused', 'uncertain', 'completed', 'observed', 'bound', 'open', 'resolved', 'not_executed', 'queued', 'starting', 'running', 'stopping', 'terminated', 'cancelling', 'cancelled', 'claimed', 'released', 'pending', 'stopped', 'requested', 'requesting', 'restart_pending', 'restart_requested', 'restart_requesting', 'restart_uncertain', 'restarted', 'checkpointed', 'replacement_registered', 'revoked', 'approved', 'rejected', 'admitted', 'executed', 'desired_state_observed'))
CONFIRMATIONS = frozenset(('operator-attested', 'independently-verified'))
INVALIDATION_REASONS = frozenset(('unspecified','cache_miss','gate_changed','artifact_unavailable','skills_unavailable','upstream_unavailable','task_changed','upstream_invalidated','upstream_changed','graph_migration'))
TABLES = {
    'graph_audit': ('stage', 'status'),
    'attempt_audit': ('stage', 'status'),
    'cache_audit': ('stage', 'status'),
    'invalidation_audit': ('stage', 'status'),
    'action_audit': (None, 'status'),
    'claim_audit': (None, 'status'),
    'job_audit': (None, 'status'),
    'lifecycle_audit': (None, 'status'),
    'manual_lifecycle_audit': (None, 'state'),
    'execution_audit': ('role', 'status'),
    'execution_turn_audit': ('role', 'status'),
    'execution_launch_audit': ('role', 'status'),
    'execution_link_audit': ('role', 'status'),
    'tool_audit': ('role', 'status'),
    'review_audit': (None, 'status'),
    'human_review_audit': (None, 'status'),
    'task_admission_audit': (None, 'status'),
    'command_audit': (None, 'status'),
    'local_scope_audit': (None, 'state'),
    'local_execution_scope_audit': (None, 'status'),
    'local_turn_scope_audit': (None, 'status'),
    'workspace_recovery_audit': (None, 'status'),
    'checkpoint_audit': (None, 'status'),
    'workspace_scope_audit': (None, 'status'),
    'local_command_launch_audit': (None, 'status'),
}


def project_event(table, row, key):
    """Return a bounded allowlisted event; never return raw identifiers or payloads."""
    if not isinstance(table, str) or table not in TABLES or not isinstance(row, dict) or type(row.get('schema_version')) is not int or row['schema_version'] != 1:
        raise ValueError('unsupported audit event')
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError('private correlation key required')
    run = row.get('run')
    event_id = row.get('id')
    created = row.get('created')
    if type(event_id) is not int or event_id < 1 or not isinstance(run, str) or not run or not isinstance(created, (int, float)) or isinstance(created, bool) or not math.isfinite(created) or created < 0:
        raise ValueError('invalid audit event')
    stage_field, status_field = TABLES[table]
    status = row.get(status_field)
    stage = row.get(stage_field) if stage_field else None
    role = row.get('role') if table in ('execution_audit','execution_turn_audit','execution_launch_audit','execution_link_audit','tool_audit','local_turn_scope_audit','local_execution_scope_audit') else None
    confirmation = row.get('confirmation')
    session = row.get('session') if table in ('execution_audit','execution_turn_audit','execution_link_audit','tool_audit','local_turn_scope_audit','local_execution_scope_audit') else None
    turn = row.get('turn') if table in ('execution_turn_audit','execution_link_audit','tool_audit','local_turn_scope_audit') else None
    command = row.get('assignment') if table == 'command_audit' else None
    execution = None
    if table in ('workspace_recovery_audit','workspace_scope_audit','local_command_launch_audit'):
        execution = row.get('assignment')
    elif table in ('execution_launch_audit','execution_link_audit','local_execution_scope_audit'):
        execution = row.get('execution')
    scope = row.get('unit') if table in ('local_scope_audit','local_turn_scope_audit','local_execution_scope_audit','workspace_recovery_audit','workspace_scope_audit','local_command_launch_audit') else None
    task = row.get('task_id') if table in ('task_admission_audit','claim_audit') else None
    attempt = [run,stage,row.get('version'),row.get('count')] if table == 'attempt_audit' and isinstance(stage,str) and isinstance(row.get('version'),str) and row['version'] and type(row.get('count')) is int and row['count']>0 else None
    return {
        'schema_version': 7,
        'kind': table,
        'event_id': hmac.new(key, (table + ':' + str(event_id)).encode('ascii'), hashlib.sha256).hexdigest(),
        'run_id': hmac.new(key, run.encode('utf-8'), hashlib.sha256).hexdigest(),
        'task_id': hmac.new(key, ('task:'+task).encode('utf-8'), hashlib.sha256).hexdigest() if isinstance(task,str) and task else None,
        'attempt_id': hmac.new(key, json.dumps(['attempt',*attempt],separators=(',',':')).encode('utf-8'), hashlib.sha256).hexdigest() if attempt else None,
        'execution_id': hmac.new(key, ('execution:'+execution).encode('utf-8'), hashlib.sha256).hexdigest() if isinstance(execution,str) and execution else None,
        'scope_id': hmac.new(key, ('scope:'+scope).encode('utf-8'), hashlib.sha256).hexdigest() if isinstance(scope,str) and scope else None,
        'session_id': hmac.new(key, ('session:'+session).encode('utf-8'), hashlib.sha256).hexdigest() if isinstance(session,str) and session else None,
        'turn_id': hmac.new(key, json.dumps(['turn',session,turn],separators=(',',':')).encode('utf-8'), hashlib.sha256).hexdigest() if isinstance(session,str) and session and isinstance(turn,str) and turn else None,
        'command_id': hmac.new(key, ('command:'+command).encode('utf-8'), hashlib.sha256).hexdigest() if isinstance(command,str) and command else None,
        'stage': stage if isinstance(stage, str) and stage in STAGES else None,
        'role': role if isinstance(role,str) and role in ROLES else None,
        'tool_kind': row.get('tool_kind') if table=='tool_audit' and isinstance(row.get('tool_kind'),str) and row['tool_kind'] in TOOL_KINDS else None,
        'input_present': bool(row['input_present']) if table=='tool_audit' and type(row.get('input_present')) is int and row['input_present'] in (0,1) else None,
        'output_present': bool(row['output_present']) if table=='tool_audit' and type(row.get('output_present')) is int and row['output_present'] in (0,1) else None,
        'status': status if isinstance(status, str) and status in STATUSES else 'unavailable',
        'confirmation': confirmation if isinstance(confirmation, str) and confirmation in CONFIRMATIONS else None,
        'reason': row.get('reason') if table == 'invalidation_audit' and isinstance(row.get('reason'),str) and row['reason'] in INVALIDATION_REASONS else None,
        'timestamp': created,
    }


def read_events(db, table, after, limit, key):
    """Read a bounded page in durable order without acknowledging export."""
    if not isinstance(table, str) or table not in TABLES or type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('invalid audit page')
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError('private correlation key required')
    cursor = db.execute(f'SELECT * FROM {table} WHERE id>? ORDER BY id LIMIT ?', (after, limit))
    columns = [item[0] for item in cursor.description]
    events = []
    last = after
    for values in cursor:
        row = dict(zip(columns, values))
        events.append(project_event(table, row, key))
        last = row['id']
    return events, last


def export_page(db, table, after, limit, key, exporter=None):
    """Best-effort export; a caller persists the returned cursor only on success."""
    if exporter is None:
        return {'status': 'disabled', 'cursor': after}
    events, last = read_events(db, table, after, limit, key)
    if not events:
        return {'status': 'empty', 'cursor': after}
    try:
        if exporter(events) is not True:
            return {'status': 'unavailable', 'cursor': after}
    except Exception:
        return {'status': 'unavailable', 'cursor': after}
    return {'status': 'exported', 'cursor': last}


def deliver_page(audit_db, cursor_db, destination, table, limit, key, exporter=None):
    """Export at least once and checkpoint only in a separate local database."""
    if exporter is None: return {'status':'disabled'}
    if not callable(exporter) or not isinstance(destination,str) or not 1<=len(destination)<=200 or not isinstance(table,str) or table not in TABLES or not isinstance(key,bytes) or len(key)<32 or type(limit) is not int or not 1<=limit<=500 or audit_db.in_transaction or cursor_db.in_transaction: raise ValueError('invalid audit delivery')
    audit_path=audit_db.execute('PRAGMA database_list').fetchone()[2]
    cursor_path=cursor_db.execute('PRAGMA database_list').fetchone()[2]
    if not audit_path or not cursor_path or os.path.samefile(audit_path,cursor_path): raise ValueError('separate durable cursor database required')
    stream=hmac.new(key,json.dumps(['audit-delivery-v1',os.path.realpath(audit_path),destination],separators=(',',':')).encode(),hashlib.sha256).hexdigest()
    cursor_db.execute('CREATE TABLE IF NOT EXISTS export_cursors(stream TEXT NOT NULL, table_name TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), PRIMARY KEY(stream,table_name))')
    cursor_db.execute('BEGIN IMMEDIATE')
    try:
        cursor_db.execute('INSERT OR IGNORE INTO export_cursors VALUES(?,?,0)',(stream,table))
        after=cursor_db.execute('SELECT position FROM export_cursors WHERE stream=? AND table_name=?',(stream,table)).fetchone()[0]
        cursor_db.execute('COMMIT')
    except Exception:
        cursor_db.execute('ROLLBACK'); raise
    result=export_page(audit_db,table,after,limit,key,exporter)
    if result['status']!='exported': return result
    cursor_db.execute('BEGIN IMMEDIATE')
    try:
        changed=cursor_db.execute('UPDATE export_cursors SET position=? WHERE stream=? AND table_name=? AND position=?',(result['cursor'],stream,table,after)).rowcount
        current=cursor_db.execute('SELECT position FROM export_cursors WHERE stream=? AND table_name=?',(stream,table)).fetchone()[0]
        cursor_db.execute('COMMIT')
    except Exception:
        cursor_db.execute('ROLLBACK'); raise
    return {'status':'exported' if changed else 'superseded','cursor':current}
