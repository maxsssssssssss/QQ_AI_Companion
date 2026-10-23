"""One bounded continuation for an empty final; no second Agent or tool loop.

Text, tool pairs and admitted user images are supported. Ordinary replies, deliberate suppression,
cancelled turns and any existing final delivery never enter this path. Receipts
contain counts/enums only; original chat and the Provider key stay in memory.
"""
import asyncio
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from . import media

NOTICE = "刚才这条没生成出有效回复，没能答上你。"
INSTRUCTION = ("上一轮没有产生可见答复。请依据本轮已有上下文、记忆与工具结果，"
               "回答当前用户的问题；保留原有人格。不要再调用工具，不要假称已找到缺失的证据。"
               "资料不足就如实说明。只输出给用户的最终回复正文。")


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS response_recoveries(
        turn_id TEXT PRIMARY KEY REFERENCES turns(turn_id), day TEXT NOT NULL,
        state TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
        reason TEXT, input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0)''')
    with db:
        db.execute("UPDATE response_recoveries SET state='unknown',reason='interrupted',finished_at=? WHERE state='pending'",
                   (stamp(),))


def stamp():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='milliseconds')


def claim(db, turn_id):
    now = stamp()
    with db:
        if db.execute('SELECT 1 FROM response_recoveries WHERE turn_id=?', (turn_id,)).fetchone():
            return False
        # Emergency continuation only: a durable cap also bounds repeated
        # malformed-provider failures; restarts and failures cannot reset it.
        count = db.execute('SELECT count(*) FROM response_recoveries WHERE day=?', (now[:10],)).fetchone()[0]
        if count >= 4:
            return False
        db.execute("INSERT INTO response_recoveries(turn_id,day,state,created_at) VALUES (?,?,'pending',?)",
                   (turn_id, now[:10], now))
    return True


def finish(db, turn_id, state, reason, usage=None):
    def tokens(key):
        value = getattr(usage, key, 0)
        return value if type(value) is int and value >= 0 else 0
    with db:
        db.execute('''UPDATE response_recoveries SET state=?,reason=?,finished_at=?,input_tokens=?,output_tokens=?
            WHERE turn_id=? AND state='pending' ''',
            (state, reason, stamp(), tokens('prompt_tokens'), tokens('completion_tokens'), turn_id))


async def recovery_context(messages):
    """Preserve original native context without making old media poison a turn.

    Native history normally contains portable data URIs. Older local/expired
    references get an explicit current/history availability marker, never an
    invented caption. No URL downloads or fallback providers are introduced.
    """
    result = []
    image_bytes = text_size = 0
    current = max((i for i, msg in enumerate(messages[:-1]) if msg.role == 'user'), default=-1)
    rows = [message.model_dump(exclude_none=True) for message in messages[:-1]]
    # Admit current/recent images first; stale history must not consume the
    # entire bounded image allowance before the current question is processed.
    candidates = [(i,j,p) for i,row in enumerate(rows) if row.get('role')=='user'
        and isinstance(row.get('content'),list) for j,p in enumerate(row['content']) if p.get('type')=='image_url']
    images = {}
    for i,j,part in sorted(candidates,key=lambda item:(-item[0],item[1]))[:media.MAX_IMAGES]:
        try:
            payload, receipt = await asyncio.to_thread(media.snapshot_ref, part.get('image_url',{}).get('url'),
                max_bytes=min(media.MAX_IMAGE_BYTES,max(0,media.MAX_TOTAL_BYTES-image_bytes)))
            image_bytes += receipt['bytes']
            images[i,j] = {'type':'image_url','image_url':{'url':payload,'detail':'high'}}
        except media.MediaUnavailable as exc:
            images[i,j] = {'type':'text','text':media.unavailable_text(str(exc),current=i==current)}
    for index, row in enumerate(rows):
        if row.get('role') == '_checkpoint':
            continue  # internal native persistence marker, not a provider role
        if row.get('role') not in {'system', 'user', 'assistant', 'tool'}:
            raise ValueError('unsupported_context')
        content = row.get('content')
        if isinstance(content, list):
            parts = []
            for part_index, part in enumerate(content):
                if part.get('type') == 'think':
                    continue
                if part.get('type') == 'text':
                    text_size += len(part.get('text', ''))
                    parts.append({'type': 'text', 'text': part.get('text', '')})
                elif part.get('type') == 'image_url':
                    reason = 'unsupported_role' if row['role']!='user' else 'image_count_limit'
                    parts.append(images.get((index,part_index),{'type':'text',
                        'text':media.unavailable_text(reason,current=index==current)}))
                else:
                    parts.append({'type': 'text', 'text': '[该历史媒体类型本轮未启用；不能声称已读取其内容。]'})
            if any(p['type'] == 'image_url' for p in parts):
                row['content'] = parts
            else:
                row['content'] = ''.join(p['text'] for p in parts) or None
        elif isinstance(content, str):
            text_size += len(content)
        text_size += len(json.dumps({k:v for k,v in row.items() if k!='content'},ensure_ascii=False))
        if text_size > 96000:
            raise ValueError('context_limit')
        # Thinking is never repurposed as the answer or exported to QQ.
        result.append({k: v for k, v in row.items() if k in {'role', 'content', 'tool_calls', 'tool_call_id'}})
    # Images have a separate bound. Include tool arguments and protocol overhead
    # in the final request bound, without treating inline bytes as prose.
    if len(json.dumps(result, ensure_ascii=False)) > 24 * 1024 * 1024:
        raise ValueError('context_limit')
    result.append({'role': 'system', 'content': INSTRUCTION})
    return result


async def continue_once(provider, messages, is_current, *, timeout=12):
    client = getattr(provider, 'client', None)
    if client is None or not hasattr(client, 'with_options'):
        raise ValueError('unsupported_provider')
    deadline = time.monotonic() + timeout
    async def wait_current(task):
        try:
            while not task.done():
                if not is_current():
                    raise asyncio.CancelledError
                if time.monotonic() >= deadline:
                    raise TimeoutError
                await asyncio.wait({task}, timeout=min(.1, max(0, deadline-time.monotonic())))
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    context = await wait_current(asyncio.create_task(recovery_context(messages)))
    if not is_current():
        raise asyncio.CancelledError
    task = asyncio.create_task(client.with_options(max_retries=0, timeout=timeout).chat.completions.create(
        model=provider.get_model(), messages=context, max_tokens=768, stream=False,
        extra_body={'thinking': {'type': 'disabled'}}))
    try:
        response = await wait_current(task)
        if not is_current():
            raise asyncio.CancelledError
        choice = response.choices[0] if response.choices else None
        body = choice.message.content if choice else None
        valid = bool(choice and choice.finish_reason == 'stop' and not choice.message.tool_calls
                     and isinstance(body, str) and body.strip() and len(body) <= 6000
                     and '<think' not in body.lower())
        return (body.strip() if valid else None), response.usage
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
