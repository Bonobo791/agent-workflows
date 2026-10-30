"""Independent structural verification; does not certify claims or quality."""
class VerificationError(RuntimeError): pass

def verify_references(store,result,expected_inputs):
    if not isinstance(result,dict) or not isinstance(expected_inputs,list) or result.get('input_versions') != expected_inputs: raise VerificationError('input versions mismatch')
    outputs=result.get('outputs')
    if not isinstance(outputs,list) or not outputs or any(not isinstance(ref,str) for ref in outputs): raise VerificationError('output references missing')
    try:
        for ref in outputs: store.get(ref)
    except Exception: raise VerificationError('output artifact unavailable or changed') from None
    return list(outputs)
