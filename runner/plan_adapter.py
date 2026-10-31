"""Normalize It's a Plan issue snapshots conservatively for workflow intake."""
from datetime import date

class PlanError(RuntimeError): pass

def inspect_issue(client,project_key,number,*,ready_columns,completed_columns,blocker_direction,external_work=None,today=None):
    if not ready_columns or not completed_columns or blocker_direction not in ('inward','outward'): raise PlanError('project-specific status and relation policy required')
    result=client.get_issue(project_key,number)
    data=result.get('data') if isinstance(result,dict) and result.get('ok') is True else None
    required=('id','identifier','title','description','columnId','archivedAt','assigneeUserId','delegateUserId','dueDate','startDate','subtasks','links','development','updatedAt')
    if not isinstance(data,dict) or any(key not in data for key in required): raise PlanError('issue snapshot incomplete')
    for field in ('assigneeUserId','delegateUserId'):
        value=data[field]
        if value is not None and (not isinstance(value,str) or not value.strip()): raise PlanError('invalid task owner')
    for field in ('startDate','dueDate'):
        value=data[field]
        if value is not None:
            try:
                if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value: raise ValueError()
            except ValueError: raise PlanError('invalid task schedule') from None
    if not all(isinstance(data[key],list) for key in ('subtasks','links','development')): raise PlanError('issue relationships unavailable')
    if any(not isinstance(child,dict) or type(child.get('archived')) is not bool for child in data['subtasks']): raise PlanError('invalid subtask status')
    attachments=client.call('list_attachments',{'issueId':data['id']})
    if not isinstance(attachments,dict) or attachments.get('ok') is not True or not isinstance(attachments.get('data'),list): raise PlanError('attachments unavailable')
    activity=client.call('list_issue_activity',{'issueId':data['id'],'filter':'comments','limit':100,'order':'asc'})
    comments=activity.get('data') if isinstance(activity,dict) and activity.get('ok') is True else None
    if not isinstance(comments,dict) or not isinstance(comments.get('items'),list) or 'nextCursor' not in comments or comments['nextCursor'] is not None: raise PlanError('complete task comments unavailable')
    if any(not isinstance(item,dict) or type(item.get('id')) is not int or item.get('issueId')!=data['id'] or not isinstance(item.get('body'),str) for item in comments['items']): raise PlanError('invalid task comments')
    dependencies=True
    for link in data['links']:
        if not isinstance(link,dict) or link.get('kind') not in ('blocks','relates','duplicates') or link.get('direction') not in ('inward','outward'): raise PlanError('invalid issue link')
        if link['kind']=='blocks' and link['direction']==blocker_direction:
            try: linked_id=link['issue']['id']
            except (KeyError,TypeError): raise PlanError('invalid blocker') from None
            linked=client.call('get_issue',{'issueId':linked_id})
            item=linked.get('data') if isinstance(linked,dict) and linked.get('ok') is True else None
            if not isinstance(item,dict) or 'columnId' not in item: raise PlanError('blocker status unavailable')
            if item['columnId'] not in completed_columns: dependencies=False
    existing=list(data['development'])+list(attachments['data'])
    if external_work is None: existing=None  # repository/PR and linked-document search not yet confirmed
    elif not isinstance(external_work,list): raise PlanError('external work discovery unavailable')
    else: existing+=external_work
    if data['startDate']:
        try: started = today is not None and date.fromisoformat(data['startDate']) <= date.fromisoformat(today)
        except (ValueError,TypeError): raise PlanError('invalid task start date') from None
    else: started=True
    return {'task_id':data['identifier'],'title':data['title'],'description':data['description'],
            'ready':data['archivedAt'] is None and data['columnId'] in ready_columns and started,
            'leaf':not any(not child.get('archived',False) for child in data['subtasks']),
            'dependencies_complete':dependencies,'owner':data['assigneeUserId'] or data['delegateUserId'],
            'due':data['dueDate'],'start':data['startDate'],'existing_work':existing,'source_version':data['updatedAt'],'comments':comments['items']}
