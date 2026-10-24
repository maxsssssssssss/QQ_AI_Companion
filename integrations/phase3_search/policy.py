"""Fail-closed public-query policy. No private-profile data belongs in this module."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import unquote, urlsplit, urlunsplit

SEARCH = "web_search_deepseek"
FETCH = "deepseek_fetch_web_page"
TOOLS = frozenset((SEARCH, FETCH))


class Rejected(ValueError):
    """A reason code, never the rejected input."""


# Reviewed mode uses a closed topic vocabulary. General mode requires literal
# current-input provenance instead; secret regexes alone do not establish it.
PUBLIC_MODIFIERS = frozenset((
    "官方", "公告", "更新", "版本", "补丁", "新卡", "机制", "文档", "发布", "最新", "新闻",
    "价格", "日期", "release", "notes", "update", "official", "documentation", "news",
    "version", "patch", "latest", "api", "search", "fetch", "中国", "国服", "国际服",
))
PRIVATE_MARKERS = re.compile(
    r"(?i)(sk-[a-z0-9_-]+|bearer\s|password|token|api.?key|secret|私钥|密码|验证码|身份证|"
    r"手机号|我(?:的|家|叫)|用户(?:资料|档案)|聊天记录|男朋友|女朋友|妈妈|爸爸|妹妹|哥哥|"
    r"[\w.+-]+@[\w.-]+\.[a-z]+|(?<!\d)\d{7,}(?!\d))"
)
NO_WEB = re.compile(r"不(?:要)?(?:上网|联网|搜索|搜了)|别(?:上网|联网|搜索|搜了)|停止搜索|取消搜索|已经解决了|do not (?:search|browse)|don't (?:search|browse)", re.I)
PRIVATE_SOURCE_INTENT = re.compile(
    r"(?:只|仅)(?:根据|依据|使用|用|查|查询).{0,24}(?:记忆|已保存|资料|档案|聊天)|"
    r"(?:你|还)(?:记得|记不记得)|(?:个人|私人|用户)(?:资料|画像|档案|记忆)|"
    r"(?:我的|本人的)(?:资料|画像|档案|偏好|生日|住址|姓名|电话|经历)|"
    r"(?:不要|不许|禁止|别).{0,20}(?:外传|外搜|对外|上传)|"
    r"(?:saved|personal|private)\s+(?:profile|memories|memory|chat)|do not share",
    re.I,
)
_QUERY_TOKEN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z0-9_][A-Za-z0-9_.+#-]*")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+\Z")
_CJK_MODIFIERS = re.compile("|".join(re.escape(word) for word in sorted(PUBLIC_MODIFIERS, key=len, reverse=True)
                                    if _CJK.fullmatch(word)))


def _grounded_query(query, source_text):
    """Do not let the Agent promote history/profile words into an external query.

    Only the caller may supply source_text. It must be the Guard's current
    inbound snapshot, never a model argument, injected prompt or saved history.
    This deliberately does not resolve historical pronouns or infer synonyms.
    """
    if not isinstance(source_text, str) or not 1 <= len(source_text.strip()) <= 32768:
        raise Rejected("query_source_unavailable")
    if NO_WEB.search(source_text):
        raise Rejected("user_forbids_web")
    if PRIVATE_SOURCE_INTENT.search(source_text):
        raise Rejected("private_source_intent")
    source = source_text.casefold()
    unmatched = _QUERY_TOKEN.sub("", query)
    if re.sub(r"[\s,，。?？/()（）-]+", "", unmatched):
        raise Rejected("unsupported_query_syntax")
    grounded = False
    for match in _QUERY_TOKEN.finditer(query):
        token = match.group().casefold()
        if token in PUBLIC_MODIFIERS:
            continue
        if _CJK.fullmatch(token):
            # Permit neutral suffixes such as 最新价格 without accepting a
            # bag-of-characters reconstruction of a private name or fact.
            pieces = [part for part in _CJK_MODIFIERS.split(token) if part]
            if not pieces:
                continue
            if any(len(part) < 2 or part not in source for part in pieces):
                raise Rejected("query_not_grounded_in_current_input")
        elif not re.search(r"(?<![a-z0-9_.+#-])" + re.escape(token) + r"(?![a-z0-9_.+#-])", source):
            raise Rejected("query_not_grounded_in_current_input")
        grounded = True
    if not grounded:
        raise Rejected("query_topic_missing")


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

    def query(self, query: object, *, source_text=None) -> str:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 128:
            raise Rejected("invalid_query")
        query = query.strip()
        if PRIVATE_MARKERS.search(query) or any(ord(c) < 32 for c in query):
            raise Rejected("private_query")
        if self.scope == "general_public":
            _grounded_query(query, source_text)
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

    async def url(self, url: object) -> str:
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
                or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url
                or parsed.query or parsed.fragment):
            raise Rejected("unsafe_url")
        if self.scope == "reviewed" and host not in self.hosts:
            raise Rejected("unreviewed_fetch_host")
        decoded_path = _decoded_url_part(parsed.path)
        decoded_host = _decoded_url_part(host)
        if (PRIVATE_MARKERS.search(decoded_path) or PRIVATE_MARKERS.search(decoded_host)):
            raise Rejected("private_url")
        if (decoded_host != host or any(ord(c) < 32 or c.isspace() for c in decoded_path)
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
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    async def arguments(self, name, arguments, *, source_text=None):
        if not isinstance(arguments, dict):
            raise Rejected("invalid_arguments")
        if name == SEARCH:
            query = self.query(arguments.get("query"), source_text=source_text)
            count = arguments.get("count", 5)
            if type(count) is not int or not 1 <= count <= 10:
                raise Rejected("invalid_count")
            # Never forward ori_question or full page content from a query. The
            # explicit Fetch boundary enforces URL policy separately.
            return {"query": query, "count": count, "need_content": False}
        if name == FETCH:
            return {"url": await self.url(arguments.get("url"))}
        raise Rejected("unapproved_tool")
