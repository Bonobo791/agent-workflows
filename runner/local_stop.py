"""Launch a detached witness for one reserved local scope stop."""
import json
from pathlib import Path
import subprocess


def start_stop_witness(path,run,unit,nonce,environment):
    manager={key:environment[key] for key in ('HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') if key in environment}
    manager['PATH']='/usr/bin:/bin'
    payload=json.dumps({'path':path,'run':run,'unit':unit,'nonce':nonce},separators=(',',':')).encode()
    if len(payload)>4096:raise ValueError('stop witness request too large')
    script=str(Path(__file__).with_name('local_stop_witness.py').resolve(strict=True))
    process=subprocess.Popen(['/usr/bin/python3','-I',script],env=manager,stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:
        if process.stdin.write(payload)!=len(payload):raise OSError('incomplete stop witness request')
        process.stdin.close()
        return process
    except Exception:
        process.stdin.close()
        raise
