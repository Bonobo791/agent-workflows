"""Serialized refresh logic with an injected, separately secured transport."""
import time
from runner.oauth import RESOURCE

class RefreshError(RuntimeError):
    pass

def refresh(store, post_form, now=None):
    with store.locked() as record:
        old=record.load()
        if old.get('refresh_pending'): raise RefreshError('prior refresh outcome unknown; reauthorization required')
        if not old.get('client_id') or old['client_id'] == 'dynamic_agent_client' or not old.get('refresh_token'): raise RefreshError('registration incomplete')
        form={'grant_type':'refresh_token','client_id':old['client_id'],'refresh_token':old['refresh_token'],'resource':RESOURCE}
        pending=dict(old); pending['refresh_pending']=True
        record.save(pending)
        try: result=post_form(form)
        except Exception: raise RefreshError('refresh outcome unknown; reauthorization may be required') from None
        if not isinstance(result,dict): raise RefreshError('invalid refresh response')
        scope=result.get('scope',old.get('scope',''))
        if (not isinstance(scope,str) or 'chatgpt.tokens.use.direct' not in scope.split()
            or not isinstance(result.get('access_token'),str) or not result['access_token']
            or not isinstance(result.get('refresh_token'),str) or not result['refresh_token']
            or result.get('token_type','').lower() != 'bearer'
            or type(result.get('expires_in')) is not int or result['expires_in'] <= 0):
            raise RefreshError('invalid refresh response; reauthorization may be required')
        updated=dict(old)
        updated.pop('refresh_pending',None)
        updated.update({key:result[key] for key in ('access_token','refresh_token','token_type','expires_in')})
        updated.update({'scope':scope, 'saved_at':time.time() if now is None else now})
        record.save(updated)
        return updated
