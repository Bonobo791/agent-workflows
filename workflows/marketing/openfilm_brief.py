"""Validate an asset brief against the frozen content and source register."""
import json
import math
from workflows.marketing.gates import CHANNELS


class BriefError(ValueError): pass


def validate_brief(store, brief, content_ref, register_ref):
    try:
        if not isinstance(brief,dict) or set(brief)!={'content_ref','assets'} or brief['content_ref']!=content_ref: raise ValueError('stale content')
        content=store.get(content_ref).decode('utf-8')
        register=json.loads(store.get(register_ref))
        claims={item['id'] for item in register['claims']}
        assets=brief['assets']
        if not isinstance(assets,list) or not 1<=len(assets)<=10: raise ValueError('asset list unavailable')
        fields={'channel','kind','width','height','duration_seconds','approved_text','claim_ids','media','captions','alt_text'}
        for asset in assets:
            if not isinstance(asset,dict) or set(asset)!=fields or asset['channel'] not in CHANNELS or asset['kind'] not in ('png','mp4'): raise ValueError('invalid asset format')
            if any(type(asset[key]) is not int or not 1<=asset[key]<=8192 for key in ('width','height')): raise ValueError('invalid dimensions')
            duration=asset['duration_seconds']
            if asset['kind']=='png' and duration is not None: raise ValueError('still duration invalid')
            if asset['kind']=='mp4' and (type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=600): raise ValueError('video duration invalid')
            texts=asset['approved_text']; ids=asset['claim_ids']
            if not isinstance(texts,list) or len(texts)>100 or any(not isinstance(v,str) or not v.strip() or v not in content for v in texts): raise ValueError('text not in frozen content')
            if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(v,str) or v not in claims for v in ids) or len(ids)!=len(set(ids)): raise ValueError('claim unavailable')
            if not isinstance(asset['alt_text'],str) or not asset['alt_text'].strip() or not isinstance(asset['captions'],str) or (asset['kind']=='mp4' and not asset['captions'].strip()): raise ValueError('accessibility text unavailable')
            if not isinstance(asset['media'],list) or len(asset['media'])>100: raise ValueError('media list invalid')
            for item in asset['media']:
                if not isinstance(item,dict) or set(item)!={'artifact_ref','rights_ref'}: raise ValueError('media or rights unavailable')
                store.get(item['artifact_ref']); store.get(item['rights_ref'])
        return True
    except Exception as exc:
        raise BriefError('asset brief invalid, stale or incomplete') from exc
