"""Send a JSON request on stdin to the private local control socket."""
import json
import os
import socket
import sys
from pathlib import Path
from runner.worker import read_secret, ConfigurationError
from runner.control import MAX_CONTROL_RESPONSE

def request_control(request):
    secret_path=os.environ.get('CONTROL_SECRET_FILE')
    if not secret_path: raise ConfigurationError('CONTROL_SECRET_FILE required')
    secret=read_secret(secret_path)
    payload=json.dumps({'secret':secret,'request':request},separators=(',',':')).encode()+b'\n'
    if len(payload)>65536: raise ConfigurationError('request too large')
    path=Path(os.environ.get('WORKFLOW_CONTROL_DIR') or os.environ.get('WORKFLOW_DATA_DIR','/data'))/'control.sock'
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(5); client.connect(str(path)); client.sendall(payload)
        with client.makefile('rb') as stream:
            raw=stream.readline(MAX_CONTROL_RESPONSE+1)
            if not raw.endswith(b'\n') or len(raw)>MAX_CONTROL_RESPONSE: raise ConfigurationError('invalid response')
            response=json.loads(raw)
    if not isinstance(response,dict) or type(response.get('ok')) is not bool: raise ConfigurationError('invalid response')
    return response

def main():
    response=request_control(json.load(sys.stdin))
    print(json.dumps(response))
    return 0 if response.get('ok') else 1

if __name__=='__main__':
    try: sys.exit(main())
    except (OSError,ValueError,ConfigurationError):
        print('Control request failed.',file=sys.stderr); sys.exit(1)
