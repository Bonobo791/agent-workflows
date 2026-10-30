"""Marketing-specific inputs, artifact ingestion and human-review packaging.

The injected worker owns execution, skill resolution, provenance and graph state.
This module never launches a process or publishes content.
"""
import json
import hashlib
import os
import stat
from pathlib import PurePosixPath
from runner.structured import parse_marketing_result
from runner.artifacts import ArtifactError
from workflows.marketing.gates import CHANNELS

class AdapterError(RuntimeError): pass

ROLE_INPUTS = {
    'intake': ('task',), 'brief': ('task',),
    'canonical_draft': ('brief',), 'canonical_review': ('brief', 'canonical_draft'),
    **{f'{channel}_draft': ('freeze',) for channel in CHANNELS},
    **{f'{channel}_review': (f'{channel}_draft',) for channel in CHANNELS},
    'asset_brief': ('freeze',),
}
PROMPTS = {
    'intake': 'Validate the task is ready and identify its requested format.',
    'brief': 'Prepare the brief and a primary-source evidence register. Do not invent claims.',
    'canonical_draft': 'Write the requested canonical asset, following the brief and page-type requirements.',
    'canonical_review': 'Independently review editorial quality, factual evidence and SEO/GEO. Return structured findings.',
    **{f'{channel}_draft': f'Create a distinct {channel} draft from the frozen source.' for channel in CHANNELS},
    **{f'{channel}_review': f'Independently review the {channel} draft and report platform-specific findings.' for channel in CHANNELS},
    'asset_brief': 'Prepare a versioned media brief; do not claim proposed media exists.',
}
PROMPTS['linkedin_review'] = 'Independently review the LinkedIn draft and report platform-specific findings.'
PROMPTS['reddit_review'] += ' Check current community rules, relevance and affiliation; block if unsuitable.'
PROMPTS['instagram_review'] += ' Missing verified media must block.'

class MarketingAdapter:
    def __init__(self, store, worker):
        self.store, self.worker = store, worker

    def run(self, run, stage, claim, refs, model):
        if stage not in ROLE_INPUTS or not isinstance(refs,dict): raise AdapterError('unsupported stage or inputs')
        selected = {}
        try:
            for name in ROLE_INPUTS[stage]:
                ref=refs[name]
                selected[name]={'ref':ref,'content':self.store.get(ref).decode('utf-8')}
        except (KeyError, ValueError, OSError, UnicodeError, ArtifactError): raise AdapterError('required artifact unavailable') from None
        inputs={'input_versions':[selected[n]['ref'] for n in ROLE_INPUTS[stage]], 'artifacts':selected}
        ref=self.worker.execute(run,stage,claim,inputs,PROMPTS[stage],model)
        try:
            stored=json.loads(self.store.get(ref).decode('utf-8'))
            public={k:v for k,v in stored.items() if not k.startswith('_')}
            result=parse_marketing_result(json.dumps(public),stage)
            if result['input_versions'] != inputs['input_versions']: raise ValueError('input mismatch')
            for output in result['outputs']: self.store.get(output)
        except Exception: raise AdapterError('invalid worker output') from None
        return ref

    def ingest(self, root, relative):
        """Ingest a regular file under a private run directory, rejecting symlinks."""
        parts=PurePosixPath(relative).parts
        if not parts or PurePosixPath(relative).is_absolute() or any(x in ('..','.') for x in parts): raise AdapterError('invalid artifact path')
        fd=None
        try:
            fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            for index,part in enumerate(parts):
                flags=os.O_RDONLY|os.O_NOFOLLOW
                if index < len(parts)-1: flags |= os.O_DIRECTORY
                else: flags |= os.O_NONBLOCK
                child=os.open(part,flags,dir_fd=fd); os.close(fd); fd=child
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size > 10*1024*1024: raise ValueError('invalid artifact')
            chunks=[]; remaining=10*1024*1024+1
            while remaining:
                chunk=os.read(fd,min(65536,remaining))
                if not chunk: break
                chunks.append(chunk); remaining-=len(chunk)
            data=b''.join(chunks)
            if len(data)>10*1024*1024: raise ValueError('artifact too large')
            return self.store.put(data)
        except (OSError,ValueError): raise AdapterError('artifact ingestion failed') from None
        finally:
            if fd is not None: os.close(fd)

    def assemble(self, results):
        required=('canonical','sources',*CHANNELS)
        if not isinstance(results,dict) or set(results)!=set(required): raise AdapterError('package incomplete')
        if any(isinstance(results[name],dict) and results[name].get('status')=='passed' for name in CHANNELS):
            if any(not isinstance(results[name],dict) or results[name].get('status')!='passed' for name in ('canonical','sources')): raise AdapterError('derivatives require passed canonical and sources')
        package={}
        for name in required:
            result=results[name]
            if not isinstance(result,dict) or result.get('status') not in ('passed','blocked','unsuitable'): raise AdapterError('invalid package status')
            refs=result.get('outputs')
            if not isinstance(refs,list) or any(not isinstance(ref,str) for ref in refs): raise AdapterError('invalid output references')
            if result['status']=='passed' and not refs: raise AdapterError('missing package content')
            if result['status']!='passed' and (not isinstance(result.get('reason'),str) or not result['reason'].strip()): raise AdapterError('missing blocked reason')
            try:
                content=[self.store.get(ref).decode('utf-8') for ref in refs]
            except (ValueError,OSError,UnicodeError,ArtifactError): raise AdapterError('package artifact unavailable') from None
            package[name]={'status':result['status'],'refs':refs,'contents':content}
            if result['status']!='passed': package[name]['reason']=result['reason']
        return self.store.put(json.dumps(package,sort_keys=True,ensure_ascii=False).encode())

class MarketingSessionAdapter:
    """Prepare role-scoped inputs for the supervised Codex executor.

    This does not advance graph gates or approve a review; its caller supplies
    the trusted output paths and independently checks the returned artifacts.
    """
    def __init__(self,store,executor,skill_resolver,required_skills):
        if skill_resolver is None or not isinstance(required_skills,dict): raise AdapterError('explicit skill configuration required')
        self.store,self.executor,self.skill_resolver,self.required_skills=store,executor,skill_resolver,required_skills
    def run(self,run,stage,claim,refs,model,output_paths,*,session_id=None,subject=None):
        if stage not in ROLE_INPUTS or stage not in self.required_skills: raise AdapterError('role or required skills unavailable')
        try:
            names=self.required_skills[stage]
            if not isinstance(names,list) or not names: raise ValueError('required skills unavailable')
            skills=self.skill_resolver.resolve(names)
            if set(skills)!=set(names): raise ValueError('required skills unavailable')
            if any(hashlib.sha256(value['content'].encode('utf-8')).hexdigest()!=value['sha256'] for value in skills.values()): raise ValueError('skill hash mismatch')
            selected={name:{'ref':refs[name],'content':self.store.get(refs[name]).decode('utf-8')} for name in ROLE_INPUTS[stage]}
            hashes={name:value['sha256'] for name,value in skills.items()}
            prompt=PROMPTS[stage]+'\n\n'+json.dumps({'inputs':selected,'skills':{name:value['content'] for name,value in skills.items()}},ensure_ascii=False)
            params=dict(run=run,role=stage,claim=claim,skills=hashes,prompt=prompt,model=model,input_versions=[selected[name]['ref'] for name in ROLE_INPUTS[stage]],output_paths=output_paths)
            if stage.endswith('_review'):
                if not subject or session_id is not None: raise ValueError('fresh subject-bound review required')
                params['prompt']+='\n\nReview subject hash: '+subject+'\nInclude this hash in input_versions.'
                return self.executor.review(subject=subject,**params)
            if subject is not None: raise ValueError('unexpected review subject')
            return self.executor.execute(session_id=session_id,**params)
        except (KeyError,ValueError,UnicodeError,ArtifactError) as error: raise AdapterError('role inputs unavailable') from error
