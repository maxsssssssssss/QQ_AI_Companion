"""Narrow image-only bridge around PrivateCompanion 6.7.0 history cleanup.

The caller scopes both hooks to the admitted private turn and the explicitly
image-capable current Provider. The plan is ephemeral on that native event;
no chat/history store or broad context replacement is introduced.
"""
import asyncio
import copy
import time

from .media import (MAX_IMAGE_BYTES, MAX_TOTAL_BYTES, MAX_IMAGES,
                    MediaUnavailable, snapshot_ref, unavailable_text)


def _row(value):
    if isinstance(value,dict):return value
    dump=getattr(value,'model_dump',None)
    return dump() if callable(dump) else None


async def capture(contexts,*,timeout=3.0):
    """Capture only existing user image positions, preferring recent images."""
    if not isinstance(contexts,list):return []
    deadline=time.monotonic()+timeout
    entries=[]
    for index,item in enumerate(contexts):
        row=_row(item)
        if not isinstance(row,dict) or row.get('role')!='user' or not isinstance(row.get('content'),list):
            continue
        parts=[_row(p) for p in row['content']]
        if any(not isinstance(p,dict) for p in parts):continue
        positions=[i for i,p in enumerate(parts) if isinstance(p,dict) and p.get('type') in {'image_url','image'}]
        if not positions:continue
        retained=[copy.deepcopy(p) for i,p in enumerate(parts) if i not in positions]
        entries.append({'index':index,'metadata':{k:copy.deepcopy(v) for k,v in row.items() if k!='content'},
            'retained':retained,'positions':positions,'original':parts,'replacements':{}})
    count=0;used=0
    for entry in reversed(entries):
        for position in entry['positions']:
            part=entry['original'][position]
            try:
                if count>=MAX_IMAGES:raise MediaUnavailable('image_count_limit')
                count+=1
                image_ref=part.get('image_url')
                ref=image_ref.get('url') if part.get('type')=='image_url' and isinstance(image_ref,dict) else None
                remaining=deadline-time.monotonic()
                if remaining<=0:raise MediaUnavailable('snapshot_timeout')
                payload,receipt=await asyncio.wait_for(asyncio.to_thread(snapshot_ref,ref,
                    max_bytes=min(MAX_IMAGE_BYTES,max(0,MAX_TOTAL_BYTES-used))),remaining)
                used+=receipt['bytes']
                restored=copy.deepcopy(part)
                restored['image_url']['url']=payload
            except (MediaUnavailable,TimeoutError) as exc:
                reason=str(exc) if isinstance(exc,MediaUnavailable) else 'snapshot_timeout'
                restored={'type':'text','text':unavailable_text(reason,current=False)}
            entry['replacements'][position]=restored
        # Do not keep a second full copy of image payloads in the event plan.
        del entry['original']
    return entries


def restore(contexts,plan):
    """Restore only an exact native cleanup result; preserve all other edits."""
    stats={'restored_images':0,'unavailable_images':0,'matched_messages':0,'unmatched_messages':0}
    if not isinstance(contexts,list):return stats
    for entry in plan or []:
        index=entry['index']
        if index>=len(contexts):
            stats['unmatched_messages']+=1;continue
        item=contexts[index];row=_row(item)
        expected=[*entry['retained'],{'type':'text','text':'[历史图片]'}]
        if (not isinstance(row,dict) or {k:v for k,v in row.items() if k!='content'}!=entry['metadata']
                or row.get('content')!=expected):
            stats['unmatched_messages']+=1;continue
        # Only image positions and the sanitizer's own placeholder change.
        # The exact retained text and row metadata are otherwise untouched.
        content=copy.deepcopy(entry['retained'])
        for position in entry['positions']:
            part=copy.deepcopy(entry['replacements'][position]);content.insert(position,part)
            stats['restored_images' if part.get('type')=='image_url' else 'unavailable_images']+=1
        if isinstance(item,dict):item['content']=content
        else:item.content=content
        stats['matched_messages']+=1
    return stats
