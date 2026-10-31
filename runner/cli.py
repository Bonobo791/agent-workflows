"""Send a JSON request on stdin to the private local control socket."""
import json
import os
import socket
import sys
from pathlib import Path
from runner.worker import read_secret, ConfigurationError
from runner.control import MAX_CONTROL_RESPONSE

ADMISSION_RESPONSE_TIMEOUT=180
class AdmissionUncertain(ConfigurationError): pass
class CheckpointUncertain(ConfigurationError): pass
class ReplacementUncertain(ConfigurationError): pass
class WithdrawalUncertain(ConfigurationError): pass

_UNCERTAIN_RESPONSES={
    'submit_configured':(AdmissionUncertain,'admission outcome unknown; inspect run status before retrying'),
    'checkpoint_configured_auto':(CheckpointUncertain,'checkpoint outcome unknown; inspect worker status before retrying'),
    'register_checkpoint_replacement':(ReplacementUncertain,'replacement outcome unknown; inspect lifecycle status before retrying'),
    'pr_withdraw':(WithdrawalUncertain,'withdrawal outcome unknown; inspect approval records before retrying'),
}
_UNCERTAIN_RESPONSES['submit_task']=_UNCERTAIN_RESPONSES['submit_configured']
_UNCERTAIN_RESPONSES['revoke_freeze_approval']=_UNCERTAIN_RESPONSES['pr_withdraw']

def _uncertain_response(request):
    if not isinstance(request,dict): return None
    action=request.get('action')
    if action=='automatic_control' and request.get('operation')=='pr_withdraw': action='pr_withdraw'
    return _UNCERTAIN_RESPONSES.get(action) if isinstance(action,str) else None

def request_control(request):
    """Send a private request and distinguish uncertain task-admission outcomes."""
    secret_path=os.environ.get('CONTROL_SECRET_FILE')
    if not secret_path: raise ConfigurationError('CONTROL_SECRET_FILE required')
    secret=read_secret(secret_path)
    payload=json.dumps({'secret':secret,'request':request},separators=(',',':')).encode()+b'\n'
    if len(payload)>65536: raise ConfigurationError('request too large')
    path=Path(os.environ.get('WORKFLOW_CONTROL_DIR') or os.environ.get('WORKFLOW_DATA_DIR','/data'))/'control.sock'
    uncertain=_uncertain_response(request)
    transmitting=False
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(5); client.connect(str(path)); transmitting=True; client.sendall(payload)
            if uncertain and uncertain[0] is AdmissionUncertain: client.settimeout(ADMISSION_RESPONSE_TIMEOUT)
            with client.makefile('rb') as stream:
                raw=stream.readline(MAX_CONTROL_RESPONSE+1)
                if not raw.endswith(b'\n') or len(raw)>MAX_CONTROL_RESPONSE: raise ConfigurationError('invalid response')
                response=json.loads(raw)
        if not isinstance(response,dict) or type(response.get('ok')) is not bool: raise ConfigurationError('invalid response')
    except (OSError,ValueError,ConfigurationError):
        if transmitting and uncertain:
            error,message=uncertain
            raise error(message) from None
        raise
    return response

def main():
    response=request_control(json.load(sys.stdin))
    print(json.dumps(response))
    return 0 if response.get('ok') else 1

if __name__=='__main__':
    try: sys.exit(main())
    except AdmissionUncertain:
        print('Admission outcome unknown; inspect run status before retrying.',file=sys.stderr); sys.exit(1)
    except CheckpointUncertain:
        print('Checkpoint outcome unknown; inspect worker status before retrying.',file=sys.stderr); sys.exit(1)
    except ReplacementUncertain:
        print('Replacement outcome unknown; inspect lifecycle status before retrying.',file=sys.stderr); sys.exit(1)
    except WithdrawalUncertain:
        print('Withdrawal outcome unknown; inspect approval records before retrying.',file=sys.stderr); sys.exit(1)
    except (OSError,ValueError,ConfigurationError):
        print('Control request failed.',file=sys.stderr); sys.exit(1)
