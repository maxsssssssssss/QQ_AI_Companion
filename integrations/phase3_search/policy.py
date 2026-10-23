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


# Queries are composed solely from explicitly reviewed public terms. Regex based
# secret detection alone cannot prove an arbitrary LLM-generated query public.
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


async def resolve_public(host: str) -> tuple[str, ...]:
    try:
        results = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, asyncio.TimeoutError) as exc:
        raise Rejected("dns_unavailable") from None
    return tuple({result[4][0] for result in results})


class PublicPolicy:
    def __init__(self, *, public_terms=(), fetch_hosts=(), resolver=resolve_public):
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

    def query(self, query: object) -> str:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 128:
            raise Rejected("invalid_query")
        query = query.strip()
        if PRIVATE_MARKERS.search(query) or any(ord(c) < 32 for c in query):
            raise Rejected("private_query")
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
                or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url
                or parsed.query or parsed.fragment):
            raise Rejected("unsafe_url")
        if host not in self.hosts:
            raise Rejected("unreviewed_fetch_host")
        if PRIVATE_MARKERS.search(unquote(parsed.path)):
            raise Rejected("private_url")
        try:
            addresses = (str(ipaddress.ip_address(host)),)
        except ValueError:
            async with asyncio.timeout(3):
                addresses = await self.resolver(host)
        if not addresses:
            raise Rejected("dns_empty")
        try:
            if not all(ipaddress.ip_address(addr).is_global for addr in addresses):
                raise Rejected("nonpublic_address")
        except ValueError:
            raise Rejected("invalid_address") from None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    async def arguments(self, name, arguments):
        if name == SEARCH:
            query = self.query(arguments.get("query"))
            count = arguments.get("count", 5)
            if type(count) is not int or not 1 <= count <= 10:
                raise Rejected("invalid_count")
            # Never forward ori_question or full page content from a query. The
            # explicit Fetch boundary enforces URL policy separately.
            return {"query": query, "count": count, "need_content": False}
        if name == FETCH:
            return {"url": await self.url(arguments.get("url"))}
        raise Rejected("unapproved_tool")
