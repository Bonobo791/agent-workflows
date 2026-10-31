"""Validate current, independently authenticated Reddit community context."""
import json
import math
import re
import time
from runner.codex_executor import _bounded
from workflows.marketing.gates import channel_issues


def verify_context(service,run,draft_ref,context_ref,timeout,cancelled):
    authority=service.reddit_context_authority
    if authority is None or not callable(getattr(authority,'verify',None)): raise ValueError('community authority unavailable')
    context=json.loads(service.artifacts.get(context_ref))
    fields={'draft_ref','community','rules_ref','checked_at','expires_at','relevant','product_mentioned','affiliation_disclosed'}
    if not isinstance(context,dict) or set(context)!=fields or context['draft_ref']!=draft_ref or not isinstance(context['community'],str) or not re.fullmatch(r'[A-Za-z0-9_]{3,21}',context['community']): raise ValueError('community context invalid')
    checked=context['checked_at']; expires=context['expires_at']; now=time.time()
    if any(type(value) not in (int,float) or not math.isfinite(value) for value in (checked,expires)) or not checked<=now<expires<=checked+86400: raise ValueError('community context expired')
    if any(type(context[key]) is not bool for key in ('relevant','product_mentioned','affiliation_disclosed')): raise ValueError('community assessment unavailable')
    service.artifacts.get(context['rules_ref'])
    if _bounded(lambda:authority.verify(run,draft_ref,context_ref),time.monotonic()+timeout,cancelled) is not True: raise ValueError('community evidence unverified')
    package={**context,'rules_current':True}
    return {'context':context,'issues':channel_issues('reddit',package)}
