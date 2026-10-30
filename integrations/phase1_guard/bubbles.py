"""A bounded loop over one model response, using the existing Guard ledger.

No independent queue, model, transport or history store. Unattempted parts are
cancelled, never replayed. Model boundaries take precedence over the optional,
bounded short-sentence fallback; neither path rewrites the reply.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import math
import random
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


def trim_boundary_newlines(text):
    """Drop CR/LF only within a bubble's whitespace-only outer edges.

    Run after native cleanup and segmentation, before planning a delivery.
    Internal line breaks (including code), horizontal spacing and emoji stay
    byte-for-byte unchanged. This is display cleanup, not another splitter;
    candidate slicing must still pass its original exact-join validation.
    """
    if not isinstance(text,str) or not text.strip():return text
    leading=len(text)-len(text.lstrip())
    trailing=len(text.rstrip())
    return (text[:leading].replace('\r','').replace('\n','')
            +text[leading:trailing]
            +text[trailing:].replace('\r','').replace('\n',''))


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


def fallback_structure_protected(text):
    """Protect actual formatting/syntax, not a topic or technical vocabulary.

    Also used read-only before native cleanup: removing code fences later must
    not make their contents eligible as casual prose. Quotation/parenthesis
    balance is checked separately on the final body, never on a saved raw body.
    """
    if not isinstance(text,str):return True
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f`~*_|<>\[\]{}\\#①②③④⑤⑥⑦⑧⑨⑩]',text):return True
    if re.search(r'https?://|www\.|mailto:|\b[A-Za-z0-9-]+\.[A-Za-z]{2,}\b',text,re.I):return True
    if re.search(r'(?:^|\s|[。！？!?：:])(?:[-*+•]|\d+[.．)）、]|[一二三四五六七八九十]+[、．])\s*|=>|==|:=',text):return True
    # Syntax-shaped code, not words such as API, bug, 配置 or 数据库.
    if re.search(r'(?m)^\s*(?:def\s+\w+\s*\(|class\s+\w+\s*[:(]|(?:const|let|var)\s+\w+\s*=)|'
                 r'\b[A-Za-z_]\w*\s*=\s*[^=]|\b[A-Za-z_]\w*\(\s*(?:["\']|\d)',text):return True
    if re.search(r'(?m)^\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\([^\n()]*\)\s*;?\s*$',text):return True
    return False


_QUOTE_PAIRS={'“':'”','‘':'’','「':'」','『':'』','"':'"',"'":"'",'(' : ')','（':'）','【':'】'}
_SENTENCE_ENDINGS='。！？!?'


def _outside_sentence_ends(text):
    """Return outside-pair end offsets, or None for ambiguous inline pairs.

    A bounded stack protects punctuation within balanced quotes/parentheses.
    Apostrophes inside words are literals. Unbalanced/multiline quoted material
    stays intact; neither newlines nor commas become guessed sentence endings.
    """
    stack=[];ends=[];i=0
    closing=set(_QUOTE_PAIRS.values())
    while i<len(text):
        char=text[i]
        if char in "'’" and i>0 and i+1<len(text) and text[i-1].isascii() and text[i-1].isalnum() and text[i+1].isascii() and text[i+1].isalnum():
            i+=1;continue
        if char in '\r\n' and stack:return None
        if stack and char==stack[-1]:
            stack.pop();i+=1;continue
        if char in _QUOTE_PAIRS:
            stack.append(_QUOTE_PAIRS[char]);i+=1;continue
        if char in closing:return None
        if not stack and char in _SENTENCE_ENDINGS:
            end=i+1
            while end<len(text):
                if text[end] in _SENTENCE_ENDINGS or text[end].isspace():
                    end+=1;continue
                emoji=_EMOJI_CLUSTER.match(text,end)
                if emoji:
                    end=emoji.end();continue
                break
            ends.append(end);i=end;continue
        i+=1
    return None if stack else ends


def _merge_adjacent(parts,*,separator=''):
    """Only fallback slices share existing separators; native model parts do not."""
    parts=list(parts)
    while len(parts)>4:
        index=min(range(len(parts)-1),key=lambda i:len(parts[i])+len(parts[i+1]))
        parts[index:index+2]=[parts[index]+separator+parts[index+1]]
    return tuple(parts)


def _smalltalk_parts(text):
    """V4: exact final-text slices at natural outside-pair sentence endings.

    Up to 160 Unicode code points, including spaces/emoji. Short reactions and
    an unpunctuated final sentence are allowed. More than four pieces merge the
    shortest adjacent pair (leftmost tie) until bounded, never truncate/rewrite.
    """
    if not isinstance(text,str) or not text.strip() or len(text)>160:return None
    if fallback_structure_protected(text):return None
    ends=_outside_sentence_ends(text)
    if not ends:return None
    if ends[-1]<len(text):ends.append(len(text))
    pieces=[text[start:end] for start,end in zip([0]+ends,ends)]
    parts=[];pending=''
    for piece in pieces:
        pending+=piece
        # No minimum word/Han count: 哈？ is a valid reaction. Avoid an empty
        # or punctuation-only bubble, attaching such prefixes/tails instead.
        if _effective_characters(pending):
            parts.append(pending);pending=''
    if pending:
        if not parts:return None
        parts[-1]+=pending
    parts=_merge_adjacent(parts)
    return tuple(parts) if 2<=len(parts)<=4 and ''.join(parts)==text else None


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
        # Native parsing protects fenced/quoted tokens. Valid markers outside
        # those structures still own boundaries; structure excludes fallback only.
        structured=bool(re.search(r'^\s*(?:```|~~~|\|.*\|\s*$)',text,re.M))
        if (enabled and native_controlled and 1<len(parsed.segments)
                and all(isinstance(part,str) and part.strip() for part in parsed.segments)):
            return plan(_merge_adjacent(parsed.segments,separator='\n'),'model_markers',
                        'native_boundaries' if len(parsed.segments)<=4 else 'native_boundaries_merged_to_four')
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
            return plan(parts,'smalltalk_fallback','bounded_sentence_boundaries_v4')
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
    minimum:float=2.0
    maximum:float=4.0
    first_minimum:float=0.0
    first_maximum:float=0.0
    def __post_init__(self):
        if not all(type(v) in (int,float) and math.isfinite(v) for v in (self.minimum,self.maximum)) or not 0<=self.minimum<=self.maximum<=4:
            raise ValueError('invalid_bubble_pacing')
        if not all(type(v) in (int,float) and math.isfinite(v) for v in (self.first_minimum,self.first_maximum)) or not 0<=self.first_minimum<=self.first_maximum<=4:
            raise ValueError('invalid_first_bubble_pacing')
    def first_delay(self):
        # Old configs remain immediate. A new plan draws only once, before its
        # first transport call and outside DeliveryService's network timeout.
        return random.uniform(self.first_minimum,self.first_maximum) if self.first_maximum else 0.0
    def delay(self,text):
        # Draw once for each later bubble, after the preceding send is confirmed.
        # Text length does not affect pacing; the existing wait remains interruptible.
        return random.uniform(self.minimum,self.maximum)


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
            delay=pacing.delay(text) if i else pacing.first_delay()
            if delay and not await wait_between(delay,is_current,sleep=sleep):
                reason='interrupted_during_interval' if i else 'interrupted_before_first';break
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
