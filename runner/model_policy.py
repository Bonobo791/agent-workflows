"""Model preference resolved from the app-server catalog, not entitlement."""
class ModelPolicyError(RuntimeError): pass

class ModelPolicy:
    display_name='GPT-6.1 Terra'
    effort='medium'
    def __init__(self): self._model=None
    def discover(self,request):
        self._model=None
        if not callable(request): raise ModelPolicyError('model discovery unavailable')
        cursor=None; seen=set(); matches=[]
        try:
            for _ in range(100):
                params={'limit':100,'includeHidden':False}
                if cursor is not None: params['cursor']=cursor
                page=request('model/list',params)
                if not isinstance(page,dict) or not isinstance(page.get('data'),list) or 'nextCursor' not in page: raise ModelPolicyError('invalid model catalog')
                for item in page['data']:
                    if not isinstance(item,dict): raise ModelPolicyError('invalid model catalog')
                    if item.get('displayName')!=self.display_name or item.get('hidden') is True: continue
                    efforts=item.get('supportedReasoningEfforts'); model=item.get('model')
                    if not isinstance(model,str) or not model or not isinstance(efforts,list) or not any(isinstance(x,dict) and x.get('reasoningEffort')==self.effort for x in efforts): continue
                    matches.append(model)
                cursor=page['nextCursor']
                if cursor is None: break
                if not isinstance(cursor,str) or not cursor or cursor in seen: raise ModelPolicyError('invalid model catalog cursor')
                seen.add(cursor)
            else: raise ModelPolicyError('model catalog incomplete')
            if len(matches)!=1: raise ModelPolicyError('GPT-6.1 Terra with medium reasoning unavailable or ambiguous')
            self._model=matches[0]; return self._model
        except ModelPolicyError: raise
        except Exception: raise ModelPolicyError('model discovery unavailable') from None
    def for_role(self,role):
        if not isinstance(role,str) or not role: raise ModelPolicyError('role required')
        if self._model is None: raise ModelPolicyError('GPT-6.1 Terra with medium reasoning not discovered')
        return {'model':self._model,'effort':self.effort}
    def require(self,role,model):
        selection=self.for_role(role)
        if model!=selection['model']: raise ModelPolicyError('configured role model does not match discovered preference')
        return selection
