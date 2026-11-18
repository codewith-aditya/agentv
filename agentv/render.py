from __future__ import annotations

import html
import re

FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)\n?(.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")
STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
THINK_OPEN_RE = re.compile(r"<think(?:ing)?>.*$", re.DOTALL | re.IGNORECASE)
CLOSE_THINK_RE = re.compile(r"</\s*think(?:ing)?\s*>", re.IGNORECASE)
PAIRED_THINK_RE = re.compile(
    r"(?is)(?:<|&lt;)\s*think(?:ing)?\s*>(.*?)</\s*think(?:ing)?\s*>"
)


def split_think(text: str) -> tuple[str, str]:
    """Return (reasoning, visible_answer).

    Gateways are inconsistent: some send `<think>…</think>`, some strip the
    angle brackets and send ` thinking…</think>`. Handle both, plus unclosed
    reasoning while streaming.
    """
    text = text or ""
    close = CLOSE_THINK_RE.search(text)
    if close:
        head = text[: close.start()]
        stripped = head.lstrip()
        opener = re.match(r"(?is)^(?:<|&lt;)?\s*think(?:ing)?\s*>?\s*", stripped)
        if opener and opener.end() > 0:
            reasoning = stripped[opener.end():]
            rest = text[close.end():]
            more_reasoning, rest = split_think(rest)
            joined = "\n".join(p for p in (reasoning.strip(), more_reasoning) if p)
            return joined, rest.strip()

    found = PAIRED_THINK_RE.findall(text)
    if found:
        cleaned = PAIRED_THINK_RE.sub("", text)
        return "\n\n".join(x.strip() for x in found if x.strip()), cleaned.strip()

    stripped = text.lstrip()
    if re.match(r"(?is)^(?:<|&lt;)\s*think(?:ing)?\s*>", stripped) or re.match(
        r"(?i)^think(?:ing)?\s*\n", stripped
    ):
        return stripped, ""
    return "", text.strip()


def strip_think(text: str) -> str:
    """Remove inline reasoning so reasoning text never leaks into the answer."""
    return split_think(text)[1]


def escape(text: str) -> str:
    return html.escape(text or "", quote=False)


def md_to_html(text: str) -> str:
    """Small, safe markdown -> Telegram HTML converter.

    Everything is HTML-escaped first, so model output can never inject tags;
    the only tags in the result are ones generated here.
    """
    text = strip_think(text or "").replace("\r\n", "\n")
    if not text.strip():
        return ""

    pre_blocks: list[str] = []

    def stash(match: re.Match[str]) -> str:
        lang = (match.group(1) or "").strip()
        code = match.group(2).rstrip("\n")
        attrs = f' class="language-{escape(lang)}"' if lang else ""
        pre_blocks.append(f"<pre><code{attrs}>{escape(code)}</code></pre>")
        return f"\x00PRE{len(pre_blocks) - 1}\x00"

    text = FENCE_RE.sub(stash, text)
    text = escape(text)

    # inline code before bold/italic so * inside code is untouched
    code_spans: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        code_spans.append(f"<code>{match.group(1)}</code>")
        return f"\x00CODE{len(code_spans) - 1}\x00"

    text = INLINE_CODE_RE.sub(stash_code, text)

    text = LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = BOLD_RE.sub(r"<b>\1</b>", text)
    text = ITALIC_RE.sub(r"<i>\1</i>", text)
    text = STRIKE_RE.sub(r"<s>\1</s>", text)
    text = HEADING_RE.sub(r"<b>\1</b>", text)

    for idx, block in enumerate(pre_blocks):
        text = text.replace(f"\x00PRE{idx}\x00", block)
    for idx, block in enumerate(code_spans):
        text = text.replace(f"\x00CODE{idx}\x00", block)
    return text.strip()


def _hard_split(text: str, limit: int) -> list[str]:
    out: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        out.append(text)
    return out


def chunk_markdown(text: str, limit: int = 3600) -> list[str]:
    """Split markdown into chunks that keep code fences balanced."""
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    open_fence = ""

    def flush() -> None:
        nonlocal current, open_fence
        body = current.rstrip()
        if open_fence:
            body += "\n```"
        if body.strip():
            chunks.append(body)
        current = f"```{open_fence}\n" if open_fence else ""

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if open_fence:
                open_fence = ""
            else:
                open_fence = stripped[3:].strip()
        if len(current) + len(line) + 1 > limit and current.strip():
            flush()
        current += line + "\n"
    if current.strip():
        body = current.rstrip()
        if open_fence:
            body += "\n```"
        chunks.append(body)
    # final safety for pathological single lines
    result: list[str] = []
    for chunk in chunks:
        if len(chunk) <= limit * 2:
            result.append(chunk)
        else:
            result.extend(_hard_split(chunk, limit))
    return result


def preview_text(text: str, limit: int = 3500) -> str:
    """Plain escaped text for live progress edits (no markdown conversion)."""
    text = strip_think(text or "")
    if len(text) > limit:
        text = text[:limit] + "\n…"
    return escape(text)


def progress_bar(ratio: float, width: int = 6) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    return "▰" * filled + "▱" * (width - filled)
