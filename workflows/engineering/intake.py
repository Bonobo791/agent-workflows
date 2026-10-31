"""Fail-closed checks for a normalized task snapshot."""
from datetime import date

def readiness(task, today):
    issues = []
    if task.get('leaf') is not True: issues.append('not a confirmed leaf')
    if task.get('ready') is not True: issues.append('not confirmed ready')
    if task.get('dependencies_complete') is not True: issues.append('dependencies unknown')
    if 'owner' not in task: issues.append('owner unknown')
    elif task['owner'] is not None: issues.append('already owned')
    if 'existing_work' not in task or task['existing_work'] is None: issues.append('existing work unknown')
    elif task['existing_work']: issues.append('existing work requires review')
    if 'due' not in task: issues.append('due date unknown')
    elif task['due'] is not None:
        try:
            due=date.fromisoformat(task['due'])
            if due.isoformat()!=task['due']: raise ValueError()
            if due < date.fromisoformat(today): issues.append('overdue requires review')
        except (ValueError, TypeError): issues.append('invalid due date')
    return issues
