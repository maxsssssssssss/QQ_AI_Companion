"""A bounded loop over one model response, using the existing Guard ledger.

No independent queue, model, transport or history store. Unattempted parts are
cancelled, never replayed. Model boundaries take precedence over the optional,
bounded short-sentence fallback; neither path rewrites the reply.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import math
import re
import unicodedata
import uuid

MARKER='<<PRIVATE_COMPANION_SPLIT>>'
PROMPT_MARKER='[Phase3 自然多气泡]'
OLD_PROMPT=('[Phase3 语义气泡]'+'日常回答通常只发一条完整消息。只有自然的兴奋、承接或补充确实需要时，'
    '可用独立一行 <<PRIVATE_COMPANION_SPLIT>> 分成 2–4 个语义完整气泡。'
    '复杂技术答复保留完整长答，不按字数机械切片。'
    '此标记只用于最终给用户的普通/最终答复，搜索过渡文本与工具参数不得含标记。')
PROMPT=(PROMPT_MARKER+"""

你正在与用户进行自然的 QQ 私聊，遵循当前已加载的人格设定。

你可以像熟悉的朋友一样连续发送几条消息。生成回复时，自主决定内容及气泡边界。

如果回复包含不同的交流动作，例如先表达反应、随后补充想法，或先调侃再继续聊天，可以自然拆成 2–3 个气泡，偶尔最多 4 个。

不要习惯性地把所有内容挤在一个气泡里。简单回应可以只有一条；完整连贯的长篇解释也可以保持一条。

每个气泡都应当是自然、可独立阅读的表达。不要机械拆句、重复内容、凑数量或为了分段而增加不必要的问题。

只在气泡之间独占一行输出：
<<PRIVATE_COMPANION_SPLIT>>

例如：

用户：今天终于下班了，累死我了。

回复：
终于舍得下班了啊（笑）
<<PRIVATE_COMPANION_SPLIT>>
今天又被工作折腾惨了？

用户：今天午饭很好吃。

回复：
那不错呀，看来今天这顿没踩雷。

示例仅说明气泡边界，不作为固定措辞，也不意味着每轮都需要追问。

代码、表格、列表和需要连续阅读的技术解释保留完整结构。搜索过渡消息及工具参数不得包含分段标记。

所有气泡仍由同一次模型生成，具体发送间隔和打断由程序处理。""")


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
    as one reply, including long technical answers. The regexes cover the reserved token's
    common escaped spellings, not arbitrary HTML or Markdown in the answer.
    """
    name=r'PRIVATE\s*_\s*COMPANION\s*_\s*SPLIT'
    left=r'(?:<|&lt;|&#0*60;|\\<)'
    right=r'(?:>|&gt;|&#0*62;|\\>)'
    cleaned=re.sub(left+r'\s*'+left+r'\s*'+name+r'\s*'+right+r'\s*'+right,
                   '',text,flags=re.I)
    cleaned=re.sub(r'\{\{\s*split_marker\s*\}\}','',cleaned,flags=re.I)
    # A damaged reserved marker is cleanup-only, never a fallback boundary.
    cleaned=re.sub(r'(?:'+left+r'\s*){0,2}'+name+r'(?:\s*'+right+r'){0,2}',
                   '',cleaned,flags=re.I)
    return cleaned.strip() if cleaned!=text else text


@dataclass(frozen=True)
class SemanticPlan:
    texts:tuple[str,...]
    source:str
    reason:str
    raw_marker_present:bool
    raw_exact_marker_count:int
    native_count:int|None
    native_controlled:bool

    def diagnostics(self):
        """Counts only; never log the generated text through this method."""
        return {'source':self.source,'reason':self.reason,
                'raw_marker_present':self.raw_marker_present,
                'raw_exact_marker_count':self.raw_exact_marker_count,
                'native_count':self.native_count,'native_controlled':self.native_controlled,
                'planned_count':len(self.texts)}


def _reserved_marker_present(text):
    return bool(re.search(r'PRIVATE[\s_\\]*COMPANION[\s_\\]*SPLIT|split_marker',text,re.I))


_EMOJI_BASE=r'[\U0001f000-\U0001faff\u2300-\u23ff\u2600-\u27bf\u2b00-\u2bff\u00a9\u00ae\u203c\u2049\u2122\u2139\u3030\u303d\u3297\u3299]'
_EMOJI_MODIFIERS=r'[\ufe0e\ufe0f\U0001f3fb-\U0001f3ff\U000e0020-\U000e007f]*'
_EMOJI_CLUSTER=re.compile(r'(?:[0-9#*]\ufe0f?\u20e3|'+_EMOJI_BASE+_EMOJI_MODIFIERS+
                          r'(?:\u200d'+_EMOJI_BASE+_EMOJI_MODIFIERS+r')*)')


def _effective_characters(text):
    # Unicode letters/numbers include Han characters. Punctuation, whitespace,
    # emoji (including numeric keycaps), selectors and joiners do not count.
    return sum(unicodedata.category(char)[0] in 'LN'
               for char in _EMOJI_CLUSTER.sub('',text))


def _smalltalk_parts(text):
    """Return exact slices of 2–4 complete, short, unstructured sentences.

    The 100-character ceiling uses Python Unicode code points, including all
    spaces and emoji components. It rejects, never truncates or selects a cut.
    Sentence endings alone select boundaries; no opening-word cue is needed.
    Sentences with fewer than six letters/numbers merge forward, with any last
    short remainder attached backward. No I/O or additional model is involved.
    """
    if not text.strip() or len(text)>100:return None
    if re.search(r'''[\x00-\x08\x0b\x0c\x0e-\x1f`~*_|<>\[\]{}\\#"'“”‘’「」『』：:①②③④⑤⑥⑦⑧⑨⑩]''',text):return None
    if re.search(r'https?://|www\.|mailto:|\b[A-Za-z0-9-]+\.[A-Za-z]{2,}\b',text,re.I):return None
    if re.search(r'(?:^|\s)(?:[-*+•]|\d+[.)、])\s*|=>|==|:=|\b\w+\([^)]*\)',text):return None
    if re.search(r'首先|其次|最后|例如|具体来说|步骤|原理|概念|命令|参数|配置|接口|事务|数据库|函数|代码|'
                 r'向量|矩阵|公式|算法|复杂度|内存|线程|进程|协议|编译|部署|'
                 r'\b(?:SQL|API|HTTP|HTTPS|SDK|JSON|XML|TCP|UDP|CPU|GPU|token|return|class|def|function)\b',text,re.I):return None
    sentences=[];start=0
    while start<len(text):
        ending=re.search(r'[。！？!?]+',text[start:])
        if ending is None:return None  # An unfinished tail is not a sentence.
        end=start+ending.end()
        # Adjacent punctuation is one ending. Trailing spaces/emoji, including
        # ZWJ, flags and keycaps, remain with that sentence without rewriting.
        while end<len(text):
            if text[end] in '。！？!?' or text[end].isspace():
                end+=1;continue
            emoji=_EMOJI_CLUSTER.match(text,end)
            if emoji:
                end=emoji.end();continue
            break
        sentences.append(text[start:end]);start=end
        if len(sentences)>4:return None
    if not 2<=len(sentences)<=4:return None
    parts=[];pending=''
    for sentence in sentences:
        pending+=sentence
        if _effective_characters(pending)>=6:
            parts.append(pending);pending=''
    if pending:
        if not parts:return None
        parts[-1]+=pending
    return tuple(parts) if 2<=len(parts)<=4 else None


@dataclass(frozen=True,slots=True)
class BubbleCandidate:
    """Only identity and boundary metadata survives the early response hook."""
    raw_sha256:str
    raw_length:int
    raw_end_offsets:tuple[int,...]
    terminal_period_removed_sha256:str|None=None
    terminal_period_removed_length:int|None=None
    terminal_period_removed_end_offsets:tuple[int,...]|None=None


def candidate_boundaries(text):
    """Record eligible raw boundaries without retaining the generated body.

    Only the pinned native cleanup's terminal Chinese-period removal is mapped.
    Other formatting/cleanup changes must keep the final answer in one bubble.
    """
    try:
        if not isinstance(text,str) or _reserved_marker_present(text):return None
        parts=_smalltalk_parts(text)
        if not parts or ''.join(parts)!=text:return None
        offsets=[];end=0
        for part in parts:
            end+=len(part);offsets.append(end)
        offsets=tuple(offsets)
        candidate=BubbleCandidate(sha(text),len(text),offsets)
        removed=re.search(r'。+(?=\s*$)',text)
        if removed is None or removed.start()<offsets[-2]:return candidate
        final=text[:removed.start()]+text[removed.end():]
        mapped=offsets[:-1]+(len(final),)
        return BubbleCandidate(candidate.raw_sha256,candidate.raw_length,offsets,
                               sha(final),len(final),mapped)
    except Exception:
        return None


def _valid_candidate_offsets(offsets,length):
    return (type(length) is int and 1<=length<=100 and type(offsets) is tuple
            and 2<=len(offsets)<=4 and all(type(end) is int for end in offsets)
            and all(start<end for start,end in zip((0,)+offsets,offsets))
            and offsets[-1]==length)


def map_candidate_boundaries(candidate,final_text):
    """Slice only the unchanged or narrowly mapped final body; never restore.

    Eligibility is not evaluated again after downstream hooks. A mismatch,
    missing body or malformed metadata returns None for a normal single send.
    """
    try:
        if type(candidate) is not BubbleCandidate or not isinstance(final_text,str):return None
        if not final_text.strip() or not 1<=len(final_text)<=100:return None
        if (not isinstance(candidate.raw_sha256,str)
                or not re.fullmatch(r'[0-9a-f]{64}',candidate.raw_sha256)
                or not _valid_candidate_offsets(candidate.raw_end_offsets,candidate.raw_length)):
            return None
        variant=(candidate.terminal_period_removed_sha256,
                 candidate.terminal_period_removed_length,
                 candidate.terminal_period_removed_end_offsets)
        if not all(value is None for value in variant):
            digest,length,offsets=variant
            if (not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest)
                    or not _valid_candidate_offsets(offsets,length)
                    or length>=candidate.raw_length
                    or offsets[:-1]!=candidate.raw_end_offsets[:-1]):
                return None
        digest=sha(final_text)
        if len(final_text)==candidate.raw_length and digest==candidate.raw_sha256:
            offsets=candidate.raw_end_offsets
        elif len(final_text)==variant[1] and digest==variant[0]:
            offsets=variant[2]
        else:return None
        parts=tuple(final_text[start:end] for start,end in zip((0,)+offsets,offsets))
        return parts if 2<=len(parts)<=4 and all(part.strip() for part in parts) and ''.join(parts)==final_text else None
    except Exception:
        return None


def semantic_plan(parser,text,*,enabled=True,fallback_enabled=False,allow_fallback=True):
    """Build an observable plan while preserving the pinned native protocol.

    Metadata contains counts and reason labels only. Callers can disable the
    heuristic for search, media or other structured replies independently of
    model-directed boundaries. Parser failure always means a readable single.
    """
    marker_present=_reserved_marker_present(text)
    exact_count=text.count(MARKER)
    native_count=None;native_controlled=False
    def plan(parts,source,reason):
        return SemanticPlan(tuple(parts),source,reason,marker_present,exact_count,
                            native_count,native_controlled)
    try:
        parsed=parser.parse_llm_segment_control(text)
        native_count=len(getattr(parsed,'segments',()))
        native_controlled=bool(parsed.controlled)
        # Preserve structured answers as one message. Native parsing/cleanup
        # still owns marker recovery, including malformed/reserved tokens.
        structured=bool(re.search(r'^\s*(?:```|~~~|\|.*\|\s*$)',text,re.M))
        if (enabled and not structured and native_controlled and 1<len(parsed.segments)<=4
                and all(isinstance(part,str) and part.strip() for part in parsed.segments)):
            return plan(parsed.segments,'model_markers','native_boundaries')
        cleaned=parsed.sanitized_text
        if not isinstance(cleaned,str) or (text.strip() and not cleaned.strip()):
            return plan([single_bubble_fallback(text)],'single','parser_invalid_output')
        if marker_present:
            return plan([single_bubble_fallback(cleaned)],'single',
                        'structured_answer' if structured else 'marker_cleanup_only')
        # No reserved control text exists here, so native normalization must
        # not change original spaces or CRLF when a single bubble is retained.
        cleaned=text
        if not enabled:return plan([cleaned],'single','multibubble_disabled')
        if not fallback_enabled:return plan([cleaned],'single','fallback_disabled')
        if not allow_fallback:return plan([cleaned],'single','fallback_context_excluded')
        parts=_smalltalk_parts(text)
        if parts and ''.join(parts)==text:
            return plan(parts,'smalltalk_fallback','bounded_complete_sentences')
        return plan([text],'single','fallback_ineligible')
    except Exception:
        # Formatting is optional. A parser/API failure cannot suppress an
        # otherwise useful answer or leak its control token to the user.
        return plan([single_bubble_fallback(text)],'single','parser_or_fallback_error')


def semantic_text(parser,text,*,enabled=True,fallback_enabled=False,allow_fallback=True):
    return list(semantic_plan(parser,text,enabled=enabled,fallback_enabled=fallback_enabled,
                              allow_fallback=allow_fallback).texts)


@dataclass(frozen=True)
class Pacing:
    minimum:float=1.0
    maximum:float=2.0
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
