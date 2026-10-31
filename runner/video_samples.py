"""Generate bounded, scope-attested visual samples from an exact MP4 artifact."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import tempfile
import time
from runner.graph import GraphStore
from runner.local_command import run_scoped_sandbox
from workflows.marketing.assets import _png_dimensions

class VideoSampleError(RuntimeError): pass

class VideoSamples:
    def __init__(self,service):
        if service.model_supervisor is None: raise VideoSampleError('local supervisor unavailable')
        self.service=service;self.supervisor=service.model_supervisor;self.db_path=service.state_db_path
        self.executable=Path('/usr/bin/ffmpeg');self.ffmpeg_hash=self._ffmpeg_hash();self.policy_hash=self._policy()
        service.graph.db.execute('''CREATE TABLE IF NOT EXISTS local_video_samples(
            id TEXT PRIMARY KEY,run TEXT NOT NULL,subject TEXT NOT NULL,asset_ref TEXT NOT NULL,unit TEXT UNIQUE,
            policy_hash TEXT NOT NULL,duration REAL NOT NULL,sample_ref TEXT,receipt_ref TEXT,state TEXT NOT NULL)''')
    def _ffmpeg_hash(self):
        fd=os.open(self.executable,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode&0o022: raise VideoSampleError('unsafe video decoder')
            with os.fdopen(fd,'rb',closefd=False) as source:return hashlib.file_digest(source,'sha256').hexdigest()
        finally:os.close(fd)
    def _policy(self):
        root=Path(__file__).parent
        data={'runtime':self.supervisor.policy_hash,'ffmpeg':self.ffmpeg_hash,'samples':12,'layout':'4x3-320-square','files':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('video_samples.py','local_command.py','local_command_child.py','adapter.py')}}
        return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def _current(self): return self._ffmpeg_hash()==self.ffmpeg_hash and self._policy()==self.policy_hash and self.supervisor.verify_policy() is True
    def sample(self,run,claim,subject,asset_ref):
        graph=GraphStore(self.db_path)
        try:
            graph._lease(run,claim);store=self.service.artifacts;manifest=json.loads(store.get(subject));matches=[item for item in manifest['assets'] if item.get('artifact_ref')==asset_ref and item.get('kind')=='mp4']
            if not matches or not self._current() or self.supervisor.preflight() is not True or self.supervisor.fence(run) is not True: raise VideoSampleError('video sample unavailable')
            duration=matches[0].get('duration_seconds')
            if type(duration) not in (int,float) or not math.isfinite(duration) or not .01<=duration<=600 or any(item.get('duration_seconds')!=duration for item in matches): raise VideoSampleError('video duration unavailable')
            data=store.get(asset_ref)
            if not 1<=len(data)<=100*1024*1024: raise VideoSampleError('invalid video artifact')
            operation=secrets.token_hex(24);root=Path(self.db_path).parent
            with tempfile.TemporaryDirectory(prefix='video-review-',dir=root) as directory:
                work=Path(directory);source=work/'input.mp4';source.write_bytes(data);source.chmod(0o400);output=work/'samples.png'
                graph.db.execute('INSERT INTO local_video_samples VALUES(?,?,?,?,NULL,?,?,NULL,NULL,?)',(operation,run,subject,asset_ref,self.policy_hash,float(duration),'reserved'))
                def record(unit):
                    db=sqlite3.connect(self.db_path,timeout=10,isolation_level=None)
                    try:
                        if db.execute("UPDATE local_video_samples SET unit=? WHERE id=? AND run=? AND state='reserved' AND unit IS NULL",(unit,operation,run)).rowcount!=1: raise VideoSampleError('video scope unavailable')
                    finally:db.close()
                interval=format(float(duration),'.6f')
                filters='fps=12/'+interval+',showinfo,scale=320:320:force_original_aspect_ratio=decrease,pad=320:320:(ow-iw)/2:(oh-ih)/2,tile=4x3:padding=2:margin=2'
                args=[str(self.executable),'-hide_banner','-v','info','-xerror','-err_detect','explode','-max_alloc','134217728','-threads','1','-filter_threads','1','-nostdin','-protocol_whitelist','file,pipe','-i',str(source),'-map','0:v:0','-vf',filters,'-frames:v','1','-n',str(output)]
                result=run_scoped_sandbox(self.supervisor,run,str(work),args,time.monotonic()+120,65536,record)
                if result.get('outcome')!='exited' or result.get('exit_code')!=0 or not self._current() or self.supervisor.fence(run) is not True: raise VideoSampleError('video sampling failed')
                lines=[line for line in result['stderr'].decode('utf-8','strict').splitlines() if 'Parsed_showinfo_' in line and ' n:' in line]
                matches=[re.search(r'\bn:\s*(\d+)\s+pts:\s*\S+\s+pts_time:([0-9.]+)',line) for line in lines]
                if len(matches)!=12 or any(match is None or int(match.group(1))!=index for index,match in enumerate(matches)): raise VideoSampleError('video sample count unavailable')
                timestamps=[float(match.group(2)) for match in matches]
                if any(not math.isfinite(value) or abs(value-index*float(duration)/12)>max(.01,float(duration)*.002) for index,value in enumerate(timestamps)): raise VideoSampleError('video sample timing unavailable')
                source_fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
                try:
                    source_info=os.fstat(source_fd)
                    if not stat.S_ISREG(source_info.st_mode) or source_info.st_uid!=os.geteuid() or source_info.st_nlink!=1 or source_info.st_size!=len(data): raise VideoSampleError('video input changed')
                    with os.fdopen(source_fd,'rb',closefd=False) as file:
                        if hashlib.file_digest(file,'sha256').hexdigest()!=asset_ref: raise VideoSampleError('video input changed')
                finally:os.close(source_fd)
                fd=os.open(output,os.O_RDONLY|os.O_NOFOLLOW)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or not 1<=info.st_size<=10485760: raise VideoSampleError('invalid sample output')
                    with os.fdopen(fd,'rb',closefd=False) as file:image=file.read(10485761)
                finally:os.close(fd)
                if _png_dimensions(image)!=(1290,968): raise VideoSampleError('invalid sample layout')
                sample_ref=store.put(image);unit=result['unit']
                graph._lease(run,claim)
                from runner.local_scope import valid_terminated_scope
                if not valid_terminated_scope(graph.db,run,unit): raise VideoSampleError('video scope not terminated')
                receipt={'run':run,'subject':subject,'asset_ref':asset_ref,'sample_ref':sample_ref,'unit':unit,'duration':float(duration),'timestamps':timestamps,'policy_hash':self.policy_hash,'coverage':'12 evenly spaced sampled frames'}
                receipt_ref=store.put(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode())
                if graph.db.execute("UPDATE local_video_samples SET sample_ref=?,receipt_ref=?,state='complete' WHERE id=? AND run=? AND unit=? AND state='reserved'",(sample_ref,receipt_ref,operation,run,unit)).rowcount!=1: raise VideoSampleError('video receipt unavailable')
                return receipt_ref
        except VideoSampleError: raise
        except Exception: raise VideoSampleError('video sampling unavailable') from None
        finally:graph.close()
    def verify(self,run,subject,asset_ref,receipt_ref):
        graph=GraphStore(self.db_path)
        try:
            store=self.service.artifacts;receipt=json.loads(store.get(receipt_ref));manifest=json.loads(store.get(subject))
            if set(receipt)!={'run','subject','asset_ref','sample_ref','unit','duration','timestamps','policy_hash','coverage'} or (receipt['run'],receipt['subject'],receipt['asset_ref'],receipt['policy_hash'],receipt['coverage'])!=(run,subject,asset_ref,self.policy_hash,'12 evenly spaced sampled frames') or not self._current(): return False
            times=receipt['timestamps'];duration=receipt['duration']
            if not isinstance(times,list) or len(times)!=12 or any(type(value) not in (int,float) or not math.isfinite(value) or abs(value-index*duration/12)>max(.01,duration*.002) for index,value in enumerate(times)): return False
            matches=[item for item in manifest['assets'] if item.get('artifact_ref')==asset_ref and item.get('kind')=='mp4']
            if not matches or any(item['duration_seconds']!=receipt['duration'] for item in matches): return False
            row=graph.db.execute("SELECT s.invocation,s.cgroup FROM local_video_samples v JOIN local_scopes s ON s.run=v.run AND s.unit=v.unit WHERE v.run=? AND v.subject=? AND v.asset_ref=? AND v.unit=? AND v.policy_hash=? AND v.duration=? AND v.sample_ref=? AND v.receipt_ref=? AND v.state='complete' AND s.state='terminated'",(run,subject,asset_ref,receipt['unit'],self.policy_hash,receipt['duration'],receipt['sample_ref'],receipt_ref)).fetchone()
            store.get(asset_ref);image=store.get(receipt['sample_ref'])
            from runner.local_scope import valid_scope_identity
            return bool(row and valid_scope_identity(receipt['unit'],*row) and _png_dimensions(image)==(1290,968))
        except Exception:return False
        finally:graph.close()
