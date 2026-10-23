"""A bounded loop over one model response, using the existing Guard ledger.

No independent queue, model, transport or history store. Unattempted parts are
cancelled, never replayed. Content decisions belong to the main model/parser.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import math
import re
import uuid

MARKER='<<PRIVATE_COMPANION_SPLIT>>'
PROMPT_MARKER='[Phase3 语义气泡]'
OLD_PROMPT=(PROMPT_MARKER+'日常回答通常只发一条完整消息。只有自然的兴奋、承接或补充确实需要时，'
    '可用独立一行 <<PRIVATE_COMPANION_SPLIT>> 分成 2–4 个语义完整气泡。'
    '复杂技术答复保留完整长答，不按字数机械切片。'
    '此标记只用于最终给用户的普通/最终答复，搜索过渡文本与工具参数不得含标记。')
PROMPT=(PROMPT_MARKER+'\n在这一次主模型回复里同时决定内容和自然停顿；人格仍遵守 V0.1。'
    '一条完整回复就够时只发一条，不必为了像聊天而拆句，也不必结尾追问。'
    '每个气泡是一条独立发出的 QQ 消息：短反应与后面的具体接话如果各自成立，就让它们分开发，'
    '不必默认把反应、调侃、补充或问题挤在同一条里；没有这种停顿时不分段。'
    '惊喜、轻轻调侃、先反应后补充等确实有不同交流动作时，可自然选择两个或三个气泡，最多四个；'
    '不用每句一泡，不重复开头或前文，不凑数量，不使用固定喜讯模板。'
    '像“饭很好吃”这样的小事接住即可，不顺势盘问细节；鼓励贴合已知事情，不空泛评价对方已经做得很好。'
    '只在气泡之间独占一行写 <<PRIVATE_COMPANION_SPLIT>>；换行或标点本身不会另发消息。'
    '代码块、表格、列表和连贯的技术解释保留完整结构，里面不要插这个控制标记。'
    '用户明确要两三个短气泡且内容合适时，使用该协议表达边界，不把“气泡一/二”写入正文。'
    '搜索前行动短句单条；工具完成后的最终答案仍可自主分段；工具参数不得出现分段标记。'
    '所有气泡一次生成，程序负责发送间隔。不要声称用户已看到尚未送达的内容。')


def sha(text):return hashlib.sha256(text.encode()).hexdigest()


def plain_qq_text(text,*,enabled=True):
    """Bounded cosmetic Markdown removal, with no renderer, I/O or model call.

    Code contents and URLs are protected. Oversized/ambiguous input is retained;
    formatting must never turn a nonempty response into a discarded send.
    """
    if not enabled or not isinstance(text,str) or len(text)>32768:return text
    try:
        output=[];fence=None
        for line in text.splitlines():
            mark=re.match(r'^ {0,3}(`{3,}|~{3,})([^`~]*)$',line)
            if mark and (fence is None or (mark[1][0]==fence[0] and len(mark[1])>=len(fence) and not mark[2].strip())):
                fence=mark[1] if fence is None else None
                continue
            if fence is not None:
                output.append(line);continue
            line=re.sub(r'^ {0,3}#{1,6}[ \t]+','',line)
            line=re.sub(r'^ {0,3}(?:>[ \t]?){1,3}','',line)
            line=re.sub(r'^ {0,3}[-*][ \t]+','• ',line)
            # Keep table cells and order; only the alignment rule is cosmetic.
            if re.fullmatch(r'\s*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?\s*',line):continue
            line=re.sub(r'\[([^\[\]\n]{1,500})\]\((https?://[^\s()]{1,2048})\)',
                lambda m:m[1]+'（'+m[2]+'）',line)
            chunks=re.split(r'(`[^`\n]*`|https?://[^\s<>）]+)',line)
            for i,chunk in enumerate(chunks):
                if chunk.startswith(('http://','https://')):continue
                if chunk.startswith('`') and chunk.endswith('`') and len(chunk)>=2:
                    chunks[i]=chunk[1:-1];continue
                chunk=re.sub(r'(?<![\\*])\*\*([^*\n]{1,8192})\*\*(?!\*)',r'\1',chunk)
                chunk=re.sub(r'(?<![\\~])~~([^~\n]{1,8192})~~(?!~)',r'\1',chunk)
                chunks[i]=chunk
            output.append(''.join(chunks))
        cleaned='\n'.join(output).strip()
        return cleaned if cleaned else text
    except Exception:
        # No exception body or response content enters diagnostics.
        return text


def single_bubble_fallback(text):
    """Remove reserved control tokens without interpreting a second protocol.

    Used only when the pinned parser is missing or throws. Content is retained
    as one reply, including long technical answers; no heuristic sentence split
    or model request is introduced. The regexes cover the reserved token's
    common escaped spellings, not arbitrary HTML or Markdown in the answer.
    """
    name=r'PRIVATE\s*_\s*COMPANION\s*_\s*SPLIT'
    left=r'(?:<|&lt;|&#0*60;|\\<)'
    right=r'(?:>|&gt;|&#0*62;|\\>)'
    cleaned=re.sub(left+r'\s*'+left+r'\s*'+name+r'\s*'+right+r'\s*'+right,
                   '',text,flags=re.I)
    cleaned=re.sub(r'\{\{\s*split_marker\s*\}\}','',cleaned,flags=re.I)
    return cleaned.strip()


def semantic_text(parser,text,*,enabled=True):
    try:
        parsed=parser.parse_llm_segment_control(text)
        # Preserve structured answers as one message. Native parsing/cleanup
        # still owns marker recovery, including malformed/reserved tokens.
        structured=bool(re.search(r'^\s*(?:```|~~~|\|.*\|\s*$)',text,re.M))
        if (enabled and not structured and parsed.controlled and 1<len(parsed.segments)<=4
                and all(isinstance(part,str) and part.strip() for part in parsed.segments)):
            return list(parsed.segments)
        cleaned=parsed.sanitized_text
        if not isinstance(cleaned,str) or (text.strip() and not cleaned.strip()):
            return [single_bubble_fallback(text)]
        return [cleaned]
    except Exception:
        # Formatting is optional. A parser/API failure cannot suppress an
        # otherwise useful answer or leak its control token to the user.
        return [single_bubble_fallback(text)]


@dataclass(frozen=True)
class Pacing:
    minimum:float=.6
    maximum:float=1.8
    def __post_init__(self):
        if not all(type(v) in (int,float) and math.isfinite(v) for v in (self.minimum,self.maximum)) or not 0<=self.minimum<=self.maximum<=3:
            raise ValueError('invalid_bubble_pacing')
    def delay(self,text):return min(self.maximum,self.minimum+len(text)/120)


async def wait_between(delay,is_current,*,sleep=asyncio.sleep):
    while delay>0:
        if not is_current():return False
        step=min(.1,delay);await sleep(step);delay-=step
    return bool(is_current())


async def send_sequence(store,*,turn_id,kind,texts,send_part,is_current,
                        generation_hash='',pacing=None,sleep=asyncio.sleep,delivery_prefix=''):
    if not texts or len(texts)>4 or kind not in {'normal','final','proactive'}:
        raise ValueError('invalid_bubble_sequence')
    pacing=pacing or Pacing()
    ids=[str(uuid.uuid5(uuid.NAMESPACE_URL,f'qq-companion:{delivery_prefix or turn_id}:{kind}:{i}')) for i in range(len(texts))]
    created=store.plan_bubbles(turn_id,kind,texts,ids,generation_hash or sha('\n'.join(texts)))
    if not created:return store.bubble_result(turn_id,kind)
    reason='sequence_incomplete'
    try:
        for i,text in enumerate(texts):
            if not is_current():reason='turn_cancelled';break
            if i and not await wait_between(pacing.delay(text),is_current,sleep=sleep):
                reason='interrupted_during_interval';break
            outcome=await send_part(i,ids[i])
            store.finish_bubble(turn_id,kind,i,outcome.state)
            if outcome.state!='sent':reason='previous_part_unconfirmed';break
        else:reason='complete'
    except asyncio.CancelledError:
        reason='task_cancelled';raise
    finally:
        # A send may have been accepted before task cancellation. Reconcile
        # its durable delivery result before cancelling only unattempted parts.
        store.cancel_remaining_bubbles(turn_id,kind,reason)
    return store.bubble_result(turn_id,kind)
