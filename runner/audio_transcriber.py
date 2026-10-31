"""Claim-bound, offline audio extraction and machine transcription."""
import hashlib
import array
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time
import wave
import sys
from runner.audio_runtime import fingerprint
from runner.graph import GraphStore
from runner.local_command import run_scoped_sandbox
from workflows.marketing.gates import CHANNELS

class AudioTranscriptionError(RuntimeError): pass

class AudioTranscriber:
    def __init__(self,service,config):
        fields={'runtime_root','python_root','model_root','fingerprint','languages'}
        if service.model_supervisor is None or not isinstance(config,dict) or set(config)!=fields or any(not isinstance(config[k],str) for k in ('runtime_root','python_root','model_root')) or not isinstance(config['fingerprint'],str) or not re.fullmatch(r'[0-9a-f]{64}',config['fingerprint']) or not isinstance(config['languages'],dict) or not config['languages'] or any(k not in CHANNELS or not isinstance(v,str) or not re.fullmatch(r'[a-z]{2}',v) for k,v in config['languages'].items()): raise AudioTranscriptionError('audio configuration invalid')
        self.service=service;self.supervisor=service.model_supervisor;self.db_path=service.state_db_path
        self.roots=[config['runtime_root'],config['python_root'],config['model_root']];self.expected=config['fingerprint'];self.languages=dict(config['languages'])
        self.python=Path(self.roots[0])/'bin'/'python-sandbox';self.ffmpeg=Path('/usr/bin/ffmpeg')
        try:
            if not self.python.is_symlink() or not self.python.resolve(strict=True).is_relative_to(Path(self.roots[1])) or not self.python.resolve(strict=True).is_file() or fingerprint(self.roots)!=self.expected: raise ValueError()
            self.ffmpeg_hash=self._ffmpeg_hash();self.policy_hash=self._policy()
        except Exception: raise AudioTranscriptionError('audio runtime unavailable or changed') from None
        service.graph.db.execute('''CREATE TABLE IF NOT EXISTS local_audio_transcripts(
            id TEXT PRIMARY KEY,run TEXT NOT NULL,subject TEXT NOT NULL,asset_ref TEXT NOT NULL,channel TEXT NOT NULL,unit TEXT UNIQUE,
            policy_hash TEXT NOT NULL,audio_ref TEXT,transcript_ref TEXT,receipt_ref TEXT,state TEXT NOT NULL)''')
    def _ffmpeg_hash(self):
        fd=os.open(self.ffmpeg,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode&0o022: raise ValueError()
            with os.fdopen(fd,'rb',closefd=False) as file:return hashlib.file_digest(file,'sha256').hexdigest()
        finally:os.close(fd)
    def _policy(self):
        root=Path(__file__).parent
        value={'runtime':self.supervisor.policy_hash,'roots':self.roots,'fingerprint':self.expected,'languages':self.languages,'ffmpeg':self.ffmpeg_hash,'files':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('audio_transcriber.py','audio_transcribe_child.py','audio_runtime.py','local_command.py','local_command_child.py','adapter.py')}}
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def _current(self):
        return self.supervisor.verify_policy() is True and self._ffmpeg_hash()==self.ffmpeg_hash and fingerprint(self.roots)==self.expected and self._policy()==self.policy_hash
    def _approved(self,db):return bool(db.execute("SELECT 1 FROM external_actions WHERE run='controller' AND action='audio_policy' AND version=? AND status='approved'",(self.policy_hash,)).fetchone())
    def ready(self):
        try:
            db=sqlite3.connect(self.db_path,timeout=10)
            try:return self._approved(db) and self._current()
            finally:db.close()
        except Exception:return False
    def supports(self,item):
        return item.get('kind')=='mp4' and isinstance(item.get('audio_streams'),list) and len(item['audio_streams'])==1 and item.get('channel') in self.languages
    @staticmethod
    def _read(path,limit):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or not 1<=info.st_size<=limit: raise ValueError()
            with os.fdopen(fd,'rb',closefd=False) as file:return file.read(limit+1)
        finally:os.close(fd)
    @staticmethod
    def _report(data,audio,language,video_duration):
        report=json.loads(data)
        if not isinstance(report,dict) or set(report)!={'language','audio_sha256','duration','peak','rms','segments'} or report['language']!=language or report['audio_sha256']!=hashlib.sha256(audio).hexdigest(): raise ValueError()
        with wave.open(io.BytesIO(audio),'rb') as wav:
            if wav.getnchannels()!=1 or wav.getsampwidth()!=2 or wav.getframerate()!=16000 or wav.getnframes()<=0: raise ValueError()
            frames=wav.getnframes();duration=frames/16000;raw=wav.readframes(frames)
        if len(raw)!=frames*2: raise ValueError()
        samples=array.array('h');samples.frombytes(raw)
        if sys.byteorder!='little':samples.byteswap()
        peak=max(abs(x) for x in samples);rms=math.sqrt(sum(x*x for x in samples)/len(samples))
        if type(report['duration']) not in (int,float) or not math.isfinite(report['duration']) or abs(report['duration']-duration)>.001 or duration>video_duration+.5 or duration>600: raise ValueError()
        if type(report['peak']) is not int or report['peak']!=peak or type(report['rms']) not in (int,float) or not math.isfinite(report['rms']) or abs(report['rms']-rms)>.001: raise ValueError()
        segments=report['segments'];previous=0;length=0
        if not isinstance(segments,list) or len(segments)>1000: raise ValueError()
        for item in segments:
            if not isinstance(item,dict) or set(item)!={'start','end','text','avg_logprob','no_speech_prob'} or any(type(item[k]) not in (int,float) or not math.isfinite(item[k]) for k in ('start','end','avg_logprob','no_speech_prob')) or not previous<=item['start']<=item['end']<=duration+.5 or not 0<=item['no_speech_prob']<=1 or not isinstance(item['text'],str): raise ValueError()
            previous=item['start'];length+=len(item['text'].encode('utf-8'))
            if length>100000: raise ValueError()
        return report
    def transcribe(self,run,claim,subject,asset_ref,channel):
        graph=GraphStore(self.db_path)
        try:
            graph._lease(run,claim);store=self.service.artifacts;manifest=json.loads(store.get(subject));matches=[item for item in manifest['assets'] if item.get('artifact_ref')==asset_ref and item.get('channel')==channel]
            if not matches or any(not self.supports(item) for item in matches) or not self._approved(graph.db) or not self._current() or self.supervisor.preflight() is not True or self.supervisor.fence(run) is not True: raise AudioTranscriptionError('audio transcription unavailable')
            duration=matches[0].get('duration_seconds')
            if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=600 or any(item.get('duration_seconds')!=duration for item in matches): raise AudioTranscriptionError('audio duration unavailable')
            data=store.get(asset_ref)
            if not 1<=len(data)<=100*1024*1024: raise AudioTranscriptionError('audio source unavailable')
            operation=secrets.token_hex(24);root=Path(self.db_path).parent
            with tempfile.TemporaryDirectory(prefix='audio-review-',dir=root) as directory:
                work=Path(directory);source=work/'input.mp4';source.write_bytes(data);source.chmod(0o400)
                helper=work/'transcribe.py';shutil.copyfile(Path(__file__).with_name('audio_transcribe_child.py'),helper);helper.chmod(0o400)
                graph.db.execute('INSERT INTO local_audio_transcripts VALUES(?,?,?,?,?,NULL,?,NULL,NULL,NULL,?)',(operation,run,subject,asset_ref,channel,self.policy_hash,'reserved'))
                def record(unit):
                    db=sqlite3.connect(self.db_path,timeout=10,isolation_level=None)
                    try:
                        if db.execute("UPDATE local_audio_transcripts SET unit=? WHERE id=? AND run=? AND state='reserved' AND unit IS NULL",(unit,operation,run)).rowcount!=1: raise AudioTranscriptionError('audio scope unavailable')
                    finally:db.close()
                args=[str(self.python),'-I',str(helper),self.roots[2],str(source),self.languages[channel],str(work)]
                result=run_scoped_sandbox(self.supervisor,run,str(work),args,time.monotonic()+300,8192,record,read_dirs=self.roots)
                if result.get('outcome')!='exited' or result.get('exit_code')!=0 or not self._current() or self.supervisor.fence(run) is not True or not self._approved(graph.db): raise AudioTranscriptionError('audio transcription failed')
                if hashlib.sha256(self._read(source,100*1024*1024)).hexdigest()!=asset_ref or self._read(helper,1048576)!=Path(__file__).with_name('audio_transcribe_child.py').read_bytes(): raise AudioTranscriptionError('audio input changed')
                audio=self._read(work/'audio.wav',20*1024*1024);report_data=self._read(work/'transcript.json',1048576)
                self._report(report_data,audio,self.languages[channel],float(duration))
                audio_ref=store.put(audio);transcript_ref=store.put(report_data);unit=result['unit'];graph._lease(run,claim)
                if not graph.db.execute("SELECT 1 FROM local_scopes WHERE run=? AND unit=? AND state='terminated'",(run,unit)).fetchone(): raise AudioTranscriptionError('audio scope not terminated')
                receipt={'run':run,'subject':subject,'asset_ref':asset_ref,'channel':channel,'audio_ref':audio_ref,'transcript_ref':transcript_ref,'unit':unit,'policy_hash':self.policy_hash,'coverage':'machine transcript and signal levels; human listening required'}
                receipt_ref=store.put(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode())
                if graph.db.execute("UPDATE local_audio_transcripts SET audio_ref=?,transcript_ref=?,receipt_ref=?,state='complete' WHERE id=? AND run=? AND unit=? AND state='reserved'",(audio_ref,transcript_ref,receipt_ref,operation,run,unit)).rowcount!=1: raise AudioTranscriptionError('audio receipt unavailable')
                return receipt_ref
        except AudioTranscriptionError: raise
        except Exception: raise AudioTranscriptionError('audio transcription unavailable') from None
        finally:graph.close()
    def verify(self,run,subject,asset_ref,channel,receipt_ref):
        graph=GraphStore(self.db_path)
        try:
            store=self.service.artifacts;receipt=json.loads(store.get(receipt_ref));manifest=json.loads(store.get(subject));matches=[item for item in manifest['assets'] if item.get('artifact_ref')==asset_ref and item.get('channel')==channel]
            if set(receipt)!={'run','subject','asset_ref','channel','audio_ref','transcript_ref','unit','policy_hash','coverage'} or (receipt['run'],receipt['subject'],receipt['asset_ref'],receipt['channel'],receipt['policy_hash'],receipt['coverage'])!=(run,subject,asset_ref,channel,self.policy_hash,'machine transcript and signal levels; human listening required') or not matches or any(not self.supports(item) for item in matches) or not self._approved(graph.db) or not self._current(): return False
            duration=matches[0].get('duration_seconds')
            if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=600 or any(item.get('duration_seconds')!=duration for item in matches): return False
            row=graph.db.execute("SELECT 1 FROM local_audio_transcripts a JOIN local_scopes s ON s.run=a.run AND s.unit=a.unit WHERE a.run=? AND a.subject=? AND a.asset_ref=? AND a.channel=? AND a.unit=? AND a.policy_hash=? AND a.audio_ref=? AND a.transcript_ref=? AND a.receipt_ref=? AND a.state='complete' AND s.state='terminated'",(run,subject,asset_ref,channel,receipt['unit'],self.policy_hash,receipt['audio_ref'],receipt['transcript_ref'],receipt_ref)).fetchone()
            store.get(asset_ref);self._report(store.get(receipt['transcript_ref']),store.get(receipt['audio_ref']),self.languages[channel],float(duration))
            return bool(row)
        except Exception:return False
        finally:graph.close()
