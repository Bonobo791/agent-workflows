"""Validate source references and independently supplied verification records.

A configured verifier is a trusted integration boundary. This module cannot
prove semantic truth, source authenticity, or completeness of model claims.
"""
import json
import re
from urllib.parse import urlsplit

class SourceError(RuntimeError): pass

def validate(store,register_ref,proof_ref):
    try:
        register=json.loads(store.get(register_ref)); proof=json.loads(store.get(proof_ref))
        if not isinstance(register,dict) or set(register)!={'claims','sources'} or not isinstance(register['claims'],list) or not register['claims'] or not isinstance(register['sources'],list) or not register['sources']: raise ValueError()
        if not isinstance(proof,dict) or set(proof)!={'register_ref','verifier_id','checks'} or proof['register_ref']!=register_ref or not isinstance(proof['verifier_id'],str) or not proof['verifier_id'].strip() or not isinstance(proof['checks'],list): raise ValueError()
        sources={}
        for source in register['sources']:
            if not isinstance(source,dict) or set(source)!={'id','url','evidence_id'} or not all(isinstance(source[k],str) and source[k].strip() for k in source): raise ValueError()
            address=urlsplit(source['url'])
            if source['id'] in sources or address.scheme!='https' or not address.hostname or address.username is not None or address.password is not None: raise ValueError()
            sources[source['id']]=source
        required=set(); claims=set()
        for claim in register['claims']:
            if not isinstance(claim,dict) or set(claim)!={'id','claim','source_ids'} or not isinstance(claim['id'],str) or not claim['id'].strip() or claim['id'] in claims or not isinstance(claim['claim'],str) or not claim['claim'].strip() or not isinstance(claim['source_ids'],list) or not claim['source_ids'] or len(claim['source_ids'])!=len(set(claim['source_ids'])): raise ValueError()
            claims.add(claim['id'])
            for source_id in claim['source_ids']:
                if source_id not in sources: raise ValueError()
                required.add((claim['id'],source_id,sources[source_id]['evidence_id']))
        seen=set()
        for check in proof['checks']:
            if not isinstance(check,dict) or set(check)!={'claim_id','source_id','evidence_id','supported','primary'} or check['supported'] is not True or check['primary'] is not True: raise ValueError()
            key=(check['claim_id'],check['source_id'],check['evidence_id'])
            if key in seen or key not in required: raise ValueError()
            seen.add(key)
        if seen!=required: raise ValueError()
        return True
    except Exception: raise SourceError('source register or independent verification unavailable') from None

def verify(store,register_ref,verifier):
    if not callable(verifier): raise SourceError('trusted source verifier unavailable')
    try:
        register=json.loads(store.get(register_ref)); result=verifier(register,register_ref)
        encoded=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        proof_ref=store.put(encoded); validate(store,register_ref,proof_ref); return proof_ref
    except Exception: raise SourceError('source verification failed') from None
