import json
REGISTER={'claims':[{'id':'claim-1','claim':'Fixture claim','source_ids':['source-1']}],'sources':[{'id':'source-1','url':'https://example.invalid/evidence','evidence_id':'evidence-1'}]}
def outputs(store,role,ref):
    return [ref,store.put(json.dumps(REGISTER).encode())] if role=='brief' else [ref]
def verifier(register,reference):
    return {'register_ref':reference,'verifier_id':'synthetic-independent-verifier','checks':[{'claim_id':'claim-1','source_id':'source-1','evidence_id':'evidence-1','supported':True,'primary':True}]}
