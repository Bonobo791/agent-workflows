"""Fixed offline speech extraction, invoked only inside the local sandbox."""
import array
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import wave


def main():
    if len(sys.argv)!=5: raise ValueError('invalid audio arguments')
    model,video,language,output=sys.argv[1:];root=Path(output)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1';os.environ['HF_HOME']=str(root/'cache')
    audio=root/'audio.wav'
    subprocess.run(['/usr/bin/ffmpeg','-v','error','-xerror','-err_detect','explode','-max_alloc','134217728','-threads','1','-nostdin','-protocol_whitelist','file,pipe','-i',video,'-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le','-n',str(audio)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120,check=True)
    with wave.open(str(audio),'rb') as wav:
        if wav.getnchannels()!=1 or wav.getsampwidth()!=2 or wav.getframerate()!=16000 or not 0<wav.getnframes()<=16000*600: raise ValueError('audio format unavailable')
        frames=wav.getnframes();raw=wav.readframes(frames)
    samples=array.array('h');samples.frombytes(raw)
    if sys.byteorder!='little': samples.byteswap()
    if len(samples)!=frames: raise ValueError('audio truncated')
    peak=max(abs(x) for x in samples);rms=math.sqrt(sum(x*x for x in samples)/len(samples))
    from faster_whisper import WhisperModel
    model_instance=WhisperModel(model,device='cpu',compute_type='int8',cpu_threads=4,local_files_only=True)
    segments,info=model_instance.transcribe(str(audio),language=language,beam_size=5,vad_filter=True,condition_on_previous_text=False)
    if info.language!=language: raise ValueError('language mismatch')
    items=[];length=0
    for segment in segments:
        text=segment.text.strip();length+=len(text.encode('utf-8'))
        if len(items)>=1000 or length>100000: raise ValueError('transcript too large')
        items.append({'start':segment.start,'end':segment.end,'text':text,'avg_logprob':segment.avg_logprob,'no_speech_prob':segment.no_speech_prob})
    with audio.open('rb') as file:audio_hash=hashlib.file_digest(file,'sha256').hexdigest()
    report={'language':language,'audio_sha256':audio_hash,'duration':frames/16000,'peak':peak,'rms':rms,'segments':items}
    (root/'transcript.json').write_text(json.dumps(report,sort_keys=True,separators=(',',':'),allow_nan=False),encoding='utf-8')

if __name__=='__main__':
    try:main()
    except Exception:
        print('Offline audio analysis failed.',file=sys.stderr);sys.exit(1)
