"""Validate an integration-provided complete review snapshot.

Comment contents are untrusted data. Classification must come from a separate
review process and valid items block advancement until fixed and re-reviewed.
"""
class TriageError(ValueError):
    pass

CATEGORIES = {'valid', 'invalid', 'duplicate', 'obsolete', 'out_of_scope'}

def review_snapshot(snapshot, commit, classifications):
    if not commit or snapshot.get('head') != commit: raise TriageError('head changed')
    checks=snapshot.get('checks')
    comments=snapshot.get('comments')
    if not isinstance(checks,list) or not checks or not isinstance(comments,list): raise TriageError('incomplete snapshot')
    if any(check.get('sha') != commit or check.get('status') != 'success' for check in checks): raise TriageError('checks not successful on commit')
    ids=[comment.get('id') for comment in comments]
    if any(not item for item in ids) or len(ids) != len(set(ids)): raise TriageError('invalid comments')
    if set(ids) != set(classifications) or any(value not in CATEGORIES for value in classifications.values()): raise TriageError('incomplete classifications')
    unresolved=snapshot.get('unresolved_change_requests')
    if not isinstance(unresolved,list): raise TriageError('invalid review decisions')
    return not unresolved and 'valid' not in classifications.values()
