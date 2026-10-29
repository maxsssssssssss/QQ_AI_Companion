"""Public query/URL boundary, not a semantic classifier for arbitrary private facts."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

SEARCH = "web_search_deepseek"
FETCH = "deepseek_fetch_web_page"
TOOLS = frozenset((SEARCH, FETCH))


class Rejected(ValueError):
    """A reason code, never the rejected input."""


# Reviewed mode retains its closed vocabulary. General mode allows the Agent
# to reformulate public topics; lexical source matching is not a privacy proof.
PUBLIC_MODIFIERS = frozenset((
    "官方", "公告", "更新", "版本", "补丁", "新卡", "机制", "文档", "发布", "最新", "新闻",
    "价格", "日期", "release", "notes", "update", "official", "documentation", "news",
    "version", "patch", "latest", "api", "search", "fetch", "中国", "国服", "国际服",
))
# Match concrete values/credential assignments, not public topic words such as
# "token pricing", "password managers", "我的世界" or family relationships.
# These patterns plus caller-supplied protected values are defence in depth;
# they cannot prove that every arbitrary query contains no private semantics.
PRIVATE_MARKERS = re.compile(
    r"(?i)(sk-[a-z0-9_-]+|-----BEGIN[ A-Z]*PRIVATE KEY-----|"
    r"\bbearer\s+[a-z0-9._~+/=-]{16,}|\bAKIA[A-Z0-9]{16}\b|"
    r"\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}|"
    r"(?:password|passwd|api[ _-]?key|(?:access[ _-]?|refresh[ _-]?)?token|secret|"
    r"密码|验证码|私钥)\s*(?:[:=：]|是|为)\s*\S+|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|"
    r"(?<!\d)(?:\+?86[- ]?)?1[3-9](?:[- ]?\d){9}(?!\d)|"
    r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
    r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dX](?!\d)|"
    r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)|"
    r"(?:QQ(?:号|号码)?|手机号|电话号码|身份证(?:号码)?)\s*(?:[:=：]|是|为)?\s*\d{5,}|"
    r"微信号\s*(?:[:=：]|是|为)\s*[a-z0-9_-]{5,}|"
    r"(?:我的|本人的|用户的)(?:姓名|住址|地址|生日|电话|经历|资料|画像)\s*(?:[:=：]|是|为)|"
    r"(?:user|assistant|用户|助手)\s*[:：]\s*.+(?:user|assistant|用户|助手)\s*[:：])"
)
NO_WEB = re.compile(r"不(?:要)?(?:上网|联网|搜索|搜了)|别(?:上网|联网|搜索|搜了)|停止搜索|取消搜索|已经解决了|do not (?:search|browse)|don't (?:search|browse)", re.I)
# Remembering a public topic or asking for official material is not a private
# memory request. Match explicit owned/saved-memory instructions instead of
# generic words such as "你记得", "资料" or "个人资料保护技术".
PRIVATE_SOURCE_INTENT = re.compile(
    r"(?:只|仅)(?:根据|依据|使用|用|查|查询)[^，。！？?!\n]{0,24}"
    r"(?:已保存(?:的)?(?:资料|信息|记忆|档案)|(?:我们|我俩|你我)(?:的)?(?:聊天|对话)|"
    r"(?:聊天|对话)(?:历史|记录)|(?:你的|已有|现有|长期|本地|保存的)(?:记忆|资料))|"
    r"(?:根据|依据|使用|读取|检索|查看|查询|用|查)(?:一下)?\s*(?:我的|本人的|我们(?:的)?)"
    r"(?:私人|个人)?(?:资料|画像|档案|记忆|聊天记录)|"
    r"(?:我的|本人的)(?:私人|个人)?(?:资料|画像|档案|记忆|偏好|生日|住址|姓名|电话|经历)|"
    r"(?:你|还)(?:记得|记不记得)[^，。！？?!\n]{0,12}(?:我的|我用的|我之前的)"
    r"[^，。！？?!\n]{0,16}(?:版本|配置|账号|账户|游戏|兴趣|习惯|工作)|"
    r"(?:不要|不许|禁止|别)[^，。！？?!\n]{0,20}(?:外传|外搜|对外|上传)|"
    r"\b(?:my|our|the user's)\s+(?:(?:saved|personal|private)\s+)?"
    r"(?:profile|memories|memory|chat(?:\s+history)?|records)\b|"
    r"\b(?:only|solely)\s+(?:use|using|from|based on)\s+(?:the\s+)?"
    r"(?:saved|stored|private)\s+(?:profile|memories|memory|chat|records)\b|"
    r"do not share",
    re.I,
)
_SENSITIVE_URL_PARAMETER = re.compile(
    r"(?i)(?:access[_-]?token|refresh[_-]?token|id[_-]?token|token|auth|authorization|"
    r"api[_-]?key|key|secret|password|passwd|signature|sig|session(?:id)?|sid|jwt|"
    r"authorization[_-]?code|auth[_-]?code|credential|x-amz-.*|x-goog-.*|email|phone|mobile|qq|wechat|idcard|ssn)\Z"
)


def _check_source(source_text):
    """Read actual inbound authority only; topic words need not occur literally."""
    if not isinstance(source_text, str) or not 1 <= len(source_text.strip()) <= 32768:
        raise Rejected("query_source_unavailable")
    if NO_WEB.search(source_text):
        raise Rejected("user_forbids_web")
    if PRIVATE_SOURCE_INTENT.search(source_text):
        raise Rejected("private_source_intent")


def _private(text, protected_values=()):
    """Check locally provided concrete secrets without exporting/storing them.

    Callers must supply only known sensitive values, not every profile word:
    public titles in a profile are not automatically private keywords.
    Tool arguments must never be used to populate this parameter.
    """
    if not isinstance(protected_values, (tuple, list, set, frozenset)) or len(protected_values) > 1024:
        raise Rejected("invalid_privacy_context")
    normalized = unicodedata.normalize("NFKC", text)
    if PRIVATE_MARKERS.search(normalized):
        return True
    folded = normalized.casefold()
    for value in protected_values:
        if not isinstance(value, str) or len(value) > 4096:
            raise Rejected("invalid_privacy_context")
        # Very short fragments are not useful secret fingerprints and otherwise
        # turn common public text into a new overly broad blacklist.
        if len(value.strip()) >= 4 and unicodedata.normalize("NFKC", value).casefold() in folded:
            return True
    return False


def _decoded_url_part(value):
    """Bound decoding before inspecting credentials and encoded controls."""
    try:
        for _ in range(4):
            decoded = unquote(value, errors="strict")
            if decoded == value:
                return decoded
            value = decoded
    except (UnicodeError, ValueError):
        raise Rejected("unsafe_url") from None
    if re.search(r"%[0-9a-f]{2}", value, re.I):
        raise Rejected("unsafe_url")
    return value


async def resolve_public(host: str) -> tuple[str, ...]:
    try:
        results = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, asyncio.TimeoutError) as exc:
        raise Rejected("dns_unavailable") from None
    return tuple({result[4][0] for result in results})


class PublicPolicy:
    def __init__(self, *, scope="reviewed", public_terms=(), fetch_hosts=(), resolver=resolve_public):
        if not isinstance(scope, str) or scope not in {"reviewed", "general_public"}:
            raise ValueError("invalid_public_scope")
        if (not isinstance(public_terms, (tuple, list)) or len(public_terms) > 64
                or any(not isinstance(t, str) or not 2 <= len(t.strip()) <= 128
                       or PRIVATE_MARKERS.search(t) or any(ord(c) < 32 for c in t) for t in public_terms)):
            raise ValueError("invalid_public_topic_configuration")
        if (not isinstance(fetch_hosts, (tuple, list)) or len(fetch_hosts) > 32
                or any(not isinstance(h, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9.])?", h)
                       for h in fetch_hosts)):
            raise ValueError("invalid_fetch_host_configuration")
        self.terms = tuple(sorted({t.strip().casefold() for t in public_terms}, key=len, reverse=True))
        self.hosts = frozenset(h.lower().rstrip('.') for h in fetch_hosts)
        self.resolver = resolver
        self.scope = scope

    def query_payload(self, query: object, *, protected_values=()) -> str:
        """Validate public payload only; the caller must separately authorize its execution context."""
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 128:
            raise Rejected("invalid_query")
        query = query.strip()
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in query):
            raise Rejected("invalid_query")
        decoded = _decoded_url_part(query)
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in decoded):
            raise Rejected("invalid_query")
        if _private(decoded, protected_values):
            raise Rejected("private_query")
        if self.scope == "general_public":
            if not any(c.isalnum() for c in query):
                raise Rejected("query_topic_missing")
            # No literal-word match, fixed synonym list or model-declared
            # permission flag: the main Agent may translate and resolve public
            # topic references, while minimising the outgoing query.
            return query
        remaining = query.casefold()
        topic_found = False
        for term in self.terms:
            if term in remaining:
                topic_found = True
                remaining = remaining.replace(term, " ")
        for word in sorted(PUBLIC_MODIFIERS, key=len, reverse=True):
            remaining = remaining.replace(word, " ")
        # Public version/date literals are allowed, but arbitrary words/names are
        # not. This intentionally rejects novel topics pending an operator edit.
        remaining = re.sub(r"(?<!\w)\d{1,4}(?:[.\-/]\d{1,4})*(?!\w)", " ", remaining)
        remaining = re.sub(r"[\s\-_,，。?？:：/()（）]+", "", remaining)
        if not topic_found or remaining:
            raise Rejected("unreviewed_public_terms")
        return query

    def query(self, query: object, *, source_text=None, protected_values=()) -> str:
        # Passive authorization remains tied to the actual inbound authority.
        # Active exploration calls query_payload only AFTER validating its own
        # bound opportunity; it must not fabricate an incoming user message.
        result = self.query_payload(query, protected_values=protected_values)
        if self.scope == "general_public":
            _check_source(source_text)
        return result

    async def url(self, url: object, *, protected_values=()) -> str:
        if not isinstance(url, str) or not 1 <= len(url) <= 2048:
            raise Rejected("invalid_url")
        try:
            parsed = urlsplit(url)
            port = parsed.port
            host = (parsed.hostname or "").lower().rstrip('.')
        except ValueError:
            raise Rejected("invalid_url") from None
        if (parsed.scheme not in ("http", "https") or not host or parsed.username is not None
                or parsed.password is not None or port not in (None, 80, 443)
                or "%" in host
                or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in url) or "\\" in url
                or (self.scope == "reviewed" and (parsed.query or parsed.fragment))):
            raise Rejected("unsafe_url")
        if self.scope == "reviewed" and host not in self.hosts:
            raise Rejected("unreviewed_fetch_host")
        decoded_path = _decoded_url_part(parsed.path)
        decoded_host = _decoded_url_part(host)
        if _private(decoded_path + " " + decoded_host, protected_values):
            raise Rejected("private_url")
        decoded_query = _decoded_url_part(parsed.query)
        decoded_fragment = _decoded_url_part(parsed.fragment)
        if _private(decoded_query + " " + decoded_fragment, protected_values):
            raise Rejected("private_url")
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in decoded_query + decoded_fragment):
            raise Rejected("unsafe_url")
        try:
            # Decode real query structure exactly once: raw '+' is a space,
            # but '%2B' is a literal plus and '%26' belongs to its value. Do
            # not parse a fully unquoted string as a new parameter structure.
            parameters = parse_qsl(parsed.query, keep_blank_values=True,
                                   max_num_fields=32, errors="strict")
        except (UnicodeError, ValueError):
            raise Rejected("unsafe_url") from None
        for key, value in parameters:
            decoded_key = _decoded_url_part(key)
            decoded_value = _decoded_url_part(value)
            if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in decoded_key + decoded_value):
                raise Rejected("unsafe_url")
            if (_SENSITIVE_URL_PARAMETER.fullmatch(unicodedata.normalize("NFKC", decoded_key))
                    or _private(decoded_key, protected_values)
                    or _private(decoded_value, protected_values)):
                raise Rejected("private_url")
        # A public filename may contain ASCII spaces encoded once as %20.
        # Keep the original URL bytes for Fetch. Literal whitespace was rejected
        # above; repeated decoding must not introduce additional hidden spaces.
        raw_path_spaces = len(re.findall(r"%20", parsed.path, re.I))
        unsafe_path_whitespace = (
            decoded_path.count(" ") != raw_path_spaces
            or any(ord(c) < 32 or 127 <= ord(c) <= 159
                   or (c.isspace() and c != " ") for c in decoded_path)
        )
        if (decoded_host != host or unsafe_path_whitespace
                or any(c in decoded_path for c in "\\?#")
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home", ".lan"))):
            raise Rejected("unsafe_url")
        try:
            addresses = (str(ipaddress.ip_address(host)),)
        except ValueError:
            if (re.fullmatch(r"[0-9.]+|0x[0-9a-f]+", host, re.I)
                    or len(host) > 253
                    or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                           for label in host.split("."))):
                raise Rejected("unsafe_url")
            async with asyncio.timeout(3):
                addresses = await self.resolver(host)
        if not addresses:
            raise Rejected("dns_empty")
        try:
            parsed_addresses = tuple(ipaddress.ip_address(addr) for addr in addresses)
        except ValueError:
            raise Rejected("invalid_address") from None
        if not all(address.is_global and not address.is_multicast and not address.is_reserved
                   for address in parsed_addresses):
            raise Rejected("nonpublic_address")
        # Preserve meaningful query bytes; dropping them could fetch a different
        # page. Fragments are client anchors, never sent in an HTTP request.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))

    async def arguments(self, name, arguments, *, source_text=None, protected_values=()):
        if not isinstance(arguments, dict):
            raise Rejected("invalid_arguments")
        if name == SEARCH:
            query = self.query(arguments.get("query"), source_text=source_text, protected_values=protected_values)
            count = arguments.get("count", 5)
            if type(count) is not int or not 1 <= count <= 10:
                raise Rejected("invalid_count")
            # Never forward ori_question or full page content from a query. The
            # explicit Fetch boundary enforces URL policy separately.
            return {"query": query, "count": count, "need_content": False}
        if name == FETCH:
            return {"url": await self.url(arguments.get("url"), protected_values=protected_values)}
        raise Rejected("unapproved_tool")
