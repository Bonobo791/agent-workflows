"""Private, claim-bound snapshots from an approved source fetch policy."""
import hashlib
import json
import time
from pathlib import Path
from runner.source_fetch import SourceFetchError, validate_content

MAX_AGE=86400

class SourceSnapshots:
    def __init__(self,service,fetcher):
        self.service,self.fetcher=service,fetcher
        self.policy_hash=self._policy()
        service.graph.db.execute('''CREATE TABLE IF NOT EXISTS source_snapshots(
            bundle TEXT PRIMARY KEY,run TEXT NOT NULL,register_ref TEXT NOT NULL,draft_ref TEXT,
            policy_hash TEXT NOT NULL,created REAL NOT NULL)''')
    def _policy(self):
        fetcher=self.fetcher
        policy={'urls':sorted(fetcher.urls),'max_bytes':fetcher.max_bytes,'timeout':fetcher.timeout,'version':1,'fetcher_sha256':hashlib.sha256(Path(__file__).with_name('source_fetch.py').read_bytes()).hexdigest(),'snapshot_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        return hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def capture(self,run,claim,register_ref,draft_ref,cancelled):
        graph=self.service.graph; store=self.service.artifacts
        if self._policy()!=self.policy_hash: raise SourceFetchError('source policy changed')
        graph._lease(run,claim)
        register=json.loads(store.get(register_ref)); sources=register.get('sources')
        if not isinstance(sources,list) or not 1<=len(sources)<=8: raise SourceFetchError('source count unavailable')
        if draft_ref is not None: store.get(draft_ref)
        items=[]; total=0; seen=set()
        for source in sources:
            if cancelled() or not isinstance(source,dict) or set(source)!={'id','url','evidence_id'} or any(not isinstance(source[k],str) or not source[k].strip() for k in source) or source['id'] in seen: raise SourceFetchError('source request invalid')
            seen.add(source['id']); fetched=self.fetcher.fetch(source['url'],cancelled=cancelled); content=fetched['content'].encode('utf-8');total+=len(content)
            try: validate_content(fetched['content_type'],content)
            except (ValueError,UnicodeError): raise SourceFetchError('source content invalid') from None
            if fetched['url']!=source['url'] or len(content)>self.fetcher.max_bytes or total>1048576 or hashlib.sha256(content).hexdigest()!=fetched['sha256']: raise SourceFetchError('source content invalid')
            items.append({**source,'content_type':fetched['content_type'],'content_ref':store.put(content)})
        if cancelled() or self._policy()!=self.policy_hash: raise SourceFetchError('source request cancelled or policy changed')
        graph._lease(run,claim)
        created=time.time();bundle={'run':run,'register_ref':register_ref,'draft_ref':draft_ref,'policy_hash':self.policy_hash,'created':created,'sources':items}
        reference=store.put(json.dumps(bundle,sort_keys=True,separators=(',',':')).encode())
        graph.db.execute('INSERT INTO source_snapshots VALUES(?,?,?,?,?,?)',(reference,run,register_ref,draft_ref,self.policy_hash,created))
        return reference
    def verify(self,run,register_ref,draft_ref,reference):
        try:
            store=self.service.artifacts; db=self.service.graph.db
            row=db.execute('SELECT run,register_ref,draft_ref,policy_hash,created FROM source_snapshots WHERE bundle=?',(reference,)).fetchone()
            bundle=json.loads(store.get(reference)); now=time.time()
            if self._policy()!=self.policy_hash or not row or row[:4]!=(run,register_ref,draft_ref,self.policy_hash) or not row[4]<=now<row[4]+MAX_AGE or bundle!={'run':run,'register_ref':register_ref,'draft_ref':draft_ref,'policy_hash':self.policy_hash,'created':row[4],'sources':bundle.get('sources')}: return False
            sources=json.loads(store.get(register_ref))['sources'];items=bundle['sources']
            if not isinstance(items,list) or len(items)!=len(sources) or not 1<=len(items)<=8: return False
            total=0
            for source,item in zip(sources,items):
                if not isinstance(item,dict) or set(item)!={'id','url','evidence_id','content_type','content_ref'} or any(item[k]!=source[k] for k in ('id','url','evidence_id')): return False
                content=store.get(item['content_ref']);total+=len(content)
                if not content or len(content)>self.fetcher.max_bytes or total>1048576: return False
                validate_content(item['content_type'],content)
            if draft_ref is not None: store.get(draft_ref)
            return True
        except Exception: return False
    def contents(self,reference):
        bundle=json.loads(self.service.artifacts.get(reference))
        return [{**item,'content':self.service.artifacts.get(item['content_ref']).decode('utf-8')} for item in bundle['sources']]
