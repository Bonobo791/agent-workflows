"""Pure marketing gates. Persistence and execution belong to the shared runner."""
from datetime import date

CHANNELS = ('linkedin', 'x', 'facebook', 'instagram', 'reddit')
STAGE_DEPENDENCIES = {
    'intake': (), 'brief': ('intake',), 'canonical_draft': ('brief',),
    'canonical_review': ('canonical_draft',), 'freeze': ('canonical_review',),
    **{f'{channel}_draft': ('freeze',) for channel in CHANNELS},
    **{f'{channel}_review': (f'{channel}_draft',) for channel in CHANNELS},
    'asset_brief': ('freeze',), 'asset_production': ('asset_brief',),
    'instagram_review': ('instagram_draft', 'asset_production'),
    'assembly': tuple(f'{channel}_review' for channel in CHANNELS) + ('asset_production',),
}


def intake_issues(task, today=None):
    issues = []
    for field in ('leaf', 'ready', 'dependencies_complete'):
        if task.get(field) is not True:
            issues.append(f'{field} not confirmed')
    if 'owner' not in task or task['owner'] is not None:
        issues.append('owner unknown or already assigned')
    if not isinstance(task.get('existing_work'), list):
        issues.append('existing work unknown')
    elif task['existing_work']:
        issues.append('existing work requires review')
    for field in ('format', 'task_id'):
        if not isinstance(task.get(field), str) or not task[field].strip():
            issues.append(f'{field} missing')
    if 'due' not in task:
        issues.append('due date unknown')
    elif task['due'] is not None:
        try:
            if date.fromisoformat(task['due']) < (today or date.today()):
                issues.append('overdue requires review')
        except (TypeError, ValueError):
            issues.append('invalid due date')
    return issues


def _finding_valid(finding):
    fields = {'id', 'severity', 'description', 'location', 'evidence', 'remedy', 'resolution'}
    if not isinstance(finding, dict) or set(finding) != fields:
        return False
    if any(not isinstance(finding[field], str) or not finding[field].strip()
           for field in fields):
        return False
    return (finding['severity'] in ('blocking', 'nonblocking') and
            finding['resolution'] in ('open', 'resolved', 'invalid', 'out_of_scope'))


def canonical_issues(reviews, writer_sessions, missing_skills, current_writer_session):
    issues = []
    if missing_skills:
        issues.append('required skills unavailable: ' + ', '.join(missing_skills))
    if not current_writer_session or current_writer_session not in writer_sessions:
        issues.append('writer session unavailable')
    used = set(writer_sessions)
    for role in ('editorial', 'factual', 'seo_geo'):
        review = reviews.get(role, {})
        session = review.get('session_id')
        if review.get('status') != 'passed':
            issues.append(f'{role} review not passed')
        if not session or session in used:
            issues.append(f'{role} reviewer session not independent')
        if session:
            used.add(session)
        findings = review.get('findings')
        if not isinstance(findings, list):
            issues.append(f'{role} findings unavailable')
        elif any(not _finding_valid(f) or (f['severity'] == 'blocking' and f['resolution'] == 'open') for f in findings):
            issues.append(f'{role} has unresolved blocking findings')
    return issues


def can_generate_social(frozen):
    return frozen.get('status') == 'passed' and bool(frozen.get('version'))


def channel_issues(channel, package):
    if channel not in CHANNELS:
        return ['unknown channel']
    issues = []
    if channel == 'instagram':
        asset = package.get('asset') or {}
        if not isinstance(asset, dict) or asset.get('status') != 'verified' or not asset.get('ref'):
            issues.append('verified asset unavailable')
    if channel == 'reddit':
        for field in ('community', 'rules_ref'):
            if not package.get(field):
                issues.append(f'{field} unavailable')
        for field in ('rules_current', 'relevant'):
            if package.get(field) is not True:
                issues.append(f'{field} not confirmed')
        if package.get('product_mentioned') not in (True, False):
            issues.append('product mention unknown')
        elif package['product_mentioned'] and package.get('affiliation_disclosed') is not True:
            issues.append('affiliation disclosure not confirmed')
    return issues


def openfilm_status(result):
    """Only a verified, referenced output is eligible for human review."""
    refs = ('brief_ref', 'project_ref', 'render_ref', 'verification_ref')
    if not all(isinstance(result.get(key), str) and result[key].strip() for key in refs):
        return 'blocked'
    return 'ready_for_review' if result.get('verified') is True else 'blocked'
