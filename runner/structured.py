"""Validate untrusted model output against the marketing result contract."""
import json
from workflows.marketing.gates import _finding_valid

class ResultError(ValueError): pass

RESULT_INSTRUCTIONS=('Your final answer must be one JSON object with fields stage, status, input_versions, outputs, and findings. '
    'Use the supplied stage and copy input_versions exactly. status must be passed, blocked, failed, or awaiting_review; '
    'include a nonempty reason if blocked or failed. outputs must be an array of SHA-256 hex strings for the requested output files in the requested order, not paths or an object. '
    'findings must be an array; use [] when there are none. Each finding must contain nonempty id, severity, description, location, evidence, remedy, and resolution strings; '
    'severity is blocking or nonblocking, and resolution is open, resolved, invalid, or out_of_scope. '
    'Do not include markdown fences or prose in the final answer.')

def parse_marketing_result(raw,expected_stage):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>1048576: raise ResultError('invalid result size')
    def invalid_constant(_): raise ResultError('invalid numeric value')
    try: value=json.loads(raw,parse_constant=invalid_constant)
    except (ValueError,TypeError): raise ResultError('invalid result JSON') from None
    allowed={'stage','status','reason','input_versions','outputs','session_id','findings'}
    required={'stage','status','input_versions','outputs','findings'}
    if not isinstance(value,dict) or not required <= value.keys() or not value.keys() <= allowed: raise ResultError('invalid result fields')
    if value['stage']!=expected_stage or not isinstance(expected_stage,str) or not expected_stage.strip(): raise ResultError('unexpected stage')
    if value['status'] not in ('passed','blocked','failed','awaiting_review'): raise ResultError('invalid status')
    for name in ('input_versions','outputs'):
        if not isinstance(value[name],list) or any(not isinstance(v,str) or not v.strip() for v in value[name]): raise ResultError('invalid references')
    if 'session_id' in value and (not isinstance(value['session_id'],str) or not value['session_id'].strip()): raise ResultError('invalid session')
    if value['status'] in ('blocked','failed') and (not isinstance(value.get('reason'),str) or not value['reason'].strip()): raise ResultError('missing reason')
    if 'reason' in value and (not isinstance(value['reason'],str) or not value['reason'].strip()): raise ResultError('invalid reason')
    if not isinstance(value['findings'],list) or any(not _finding_valid(item) for item in value['findings']): raise ResultError('invalid findings')
    if value['status']=='passed' and any(item['severity']=='blocking' and item['resolution']=='open' for item in value['findings']): raise ResultError('unresolved blocking finding')
    return value
