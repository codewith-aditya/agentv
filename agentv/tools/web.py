from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
from urllib.parse import parse_qs, urlparse

import httpx

from .base import Tool, ToolContext, ToolResult

TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
ANY_TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
NL_RE = re.compile(r"\n{3,}")


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _html_to_text(raw: str) -> str:
    raw = TAG_RE.sub(" ", raw)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", raw, flags=re.IGNORECASE)
    raw = ANY_TAG_RE.sub("", raw)
    raw = html.unescape(raw)
    raw = WS_RE.sub(" ", raw)
    raw = NL_RE.sub("\n\n", raw)
    return raw.strip()


async def _bing_search(query: str, count: int) -> ToolResult:
    """Keyless fallback: Bing HTML results."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers=headers) as client:
        resp = await client.get("https://www.bing.com/search", params={"q": query, "count": count})
    if resp.status_code != 200:
        return ToolResult(f"Search failed: HTTP {resp.status_code}", is_error=True)
    blocks = re.findall(r'<li class="b_algo".*?</li>', resp.text, re.S)
    lines: list[str] = []
    for block in blocks[:count]:
        title_m = re.search(r"<h2[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not title_m:
            continue
        cite_m = re.search(r"<cite[^>]*>(.*?)</cite>", block, re.S)
        snippet_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        title = _html_to_text(title_m.group(2))
        url = _html_to_text(cite_m.group(1)) if cite_m else title_m.group(1)
        snippet = _html_to_text(snippet_m.group(1)) if snippet_m else ""
        lines.append(f"- {title}\n  {url}\n  {snippet}")
    return ToolResult("\n".join(lines) or "No results.")


async def _ddg_search(query: str, count: int) -> ToolResult:
    """Keyless fallback: DuckDuckGo HTML endpoint."""
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 AgentV/2.0"}
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        resp = await client.post(
            "https://html.duckduckgo.com/html/", data={"q": query}, headers=headers
        )
        if resp.status_code != 200:
            resp = await client.get(
                "https://lite.duckduckgo.com/lite/", params={"q": query}, headers=headers
            )
    if resp.status_code != 200:
        return ToolResult(f"Search failed: HTTP {resp.status_code}", is_error=True)
    html_text = resp.text
    links = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', html_text, re.S)
    lines: list[str] = []
    for idx, (href, title) in enumerate(links[:count]):
        snippet = _html_to_text(snippets[idx]) if idx < len(snippets) else ""
        clean_title = _html_to_text(title)
        if href.startswith("//duckduckgo.com/l/"):
            parsed = urlparse("https:" + href)
            target = parse_qs(parsed.query).get("uddg", [href])[0]
        else:
            target = href
        lines.append(f"- {clean_title}\n  {target}\n  {snippet}")
    return ToolResult("\n".join(lines) or "No results.")


async def _web_search(ctx: ToolContext, query: str = "", count: int = 5) -> ToolResult:
    if not (query or "").strip():
        return ToolResult("query is required", is_error=True)
    count = min(max(int(count or 5), 1), 10)
    if not ctx.cfg.brave_api_key:
        return await _bing_search(query, count)
    params = {"q": query, "count": count}
    headers = {"X-Subscription-Token": ctx.cfg.brave_api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.get("https://api.search.brave.com/res/v1/web/search", params=params, headers=headers)
    if resp.status_code >= 400:
        return ToolResult(f"Search failed: HTTP {resp.status_code} {resp.text[:200]}", is_error=True)
    data = resp.json()
    results = (data.get("web") or {}).get("results") or []
    lines = []
    for item in results[:count]:
        lines.append(
            f"- {item.get('title', '')}\n  {item.get('url', '')}\n  {item.get('description', '')}"
        )
    return ToolResult("\n".join(lines) or "No results.")


async def _web_fetch(ctx: ToolContext, url: str = "", max_chars: int = 8000) -> ToolResult:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ToolResult("Only http(s) URLs are allowed.", is_error=True)
    if not _is_public_host(parsed.hostname or ""):
        return ToolResult("Refusing to fetch private/internal addresses.", is_error=True)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AgentV/2.0)"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        return ToolResult(f"HTTP {resp.status_code} fetching {url}", is_error=True)
    ctype = resp.headers.get("content-type", "")
    limit = min(max(int(max_chars or 8000), 500), 30000)
    if "application/json" in ctype:
        return ToolResult(json.dumps(resp.json(), indent=2)[:limit])
    text = _html_to_text(resp.text)
    return ToolResult(text[:limit] or "(empty page)")


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="web_search",
            description="Search the web (Brave) and return titles, URLs and snippets.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}, "count": {"type": "integer"}},
                "required": ["query"],
            },
            handler=_web_search,
            min_role="user",
        ),
        Tool(
            name="web_fetch",
            description="Fetch a public web page and return its readable text.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer"}},
                "required": ["url"],
            },
            handler=_web_fetch,
            min_role="user",
        ),
    ]
