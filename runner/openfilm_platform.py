"""Version-bound, time-limited evidence for channel asset constraints."""
import json
import math
import time
from runner.codex_executor import _bounded

MAX_AGE=86400

def verify_platform_block(service,run,record,timeout=30):
    """Recheck any source-backed block before presenting it as current evidence."""
    try:
        if not isinstance(record,dict): return False
        if 'platform_block_ref' not in record and 'platform_review' not in record: return True
        if record.get('status')!='blocked' or not isinstance(record.get('platform_block_ref'),str): return False
        state=service.graph.get(run,'asset_brief')
        if not state or state['status']!='passed': return False
        outputs=json.loads(service.artifacts.get(state['version']))['outputs']
        if not isinstance(outputs,list) or len(outputs)!=1: return False
        brief_ref=outputs[0]; brief=json.loads(service.artifacts.get(brief_ref))
        evidence=json.loads(service.artifacts.get(record['platform_block_ref']))
        authority=service.openfilm_platform_authority
        if evidence!=record.get('platform_review') or evidence.get('brief_ref')!=brief_ref or not callable(getattr(authority,'verify_blocked',None)): return False
        return _bounded(lambda:authority.verify_blocked(run,brief_ref,brief,record['platform_block_ref']),time.monotonic()+timeout,lambda:False) is True
    except Exception: return False


def verify_platform(service,run,brief_ref,brief,proof_ref,timeout,cancelled):
    """Require a trusted authority to authenticate every current source."""
    authority=service.openfilm_platform_authority
    if authority is None or not callable(getattr(authority,'verify',None)): raise ValueError('platform authority unavailable')
    proof=json.loads(service.artifacts.get(proof_ref)); now=time.time()
    if not isinstance(proof,dict) or set(proof)!={'brief_ref','checked_at','expires_at','assets'} or proof['brief_ref']!=brief_ref: raise ValueError('platform evidence invalid')
    checked=proof['checked_at']; expires=proof['expires_at']
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in (checked,expires)) or not checked<=now<expires<=checked+MAX_AGE: raise ValueError('platform evidence expired')
    items=proof['assets']
    if not isinstance(items,list) or len(items)!=len(brief['assets']): raise ValueError('platform evidence incomplete')
    fields=('channel','kind','width','height','duration_seconds')
    for item,spec in zip(items,brief['assets']):
        if not isinstance(item,dict) or set(item)!=set(fields)|{'source_ref','supported'} or item['supported'] is not True or any(item[key]!=spec[key] or type(item[key]) is not type(spec[key]) for key in fields): raise ValueError('platform constraint mismatch')
        service.artifacts.get(item['source_ref'])
    if _bounded(lambda:authority.verify(run,brief_ref,brief,proof_ref),time.monotonic()+timeout,cancelled) is not True: raise ValueError('platform source unverified')
    return True
