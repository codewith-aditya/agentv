"""Telegram Bot API 10.x Rich Message rendering.

Rich messages (Bot API 10.1+, Aug 2026) support real headings, tables,
expandable details, task lists, math and native draft streaming. This module
turns the model's GitHub-flavoured markdown into the rich HTML dialect, and
degrades to plain Telegram HTML when a client/API does not support it.

Docs: https://core.telegram.org/bots/api/rich-messages
"""

from __future__ import annotations

import re

from .render import escape, strip_think

MAX_RICH_CHARS = 32000

FENCE_RE = re.compile(r"^```([A-Za-z0-9_+.#-]*)\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
TASK_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*)$")
UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
OL_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
HR_RE = re.compile(r"^\s*(?:---+|\*\*\*+|___+)\s*$")
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
MATH_BLOCK_RE = re.compile(r"^\s*\$\$(.+?)\$\$\s*$")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
LINK_RE = re.compile(r"\[([^\]\n]+)\]\(((?:https?|tg|mailto|tel):[^\s)]+)\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")
STRIKE_RE = re.compile(r"~~(.+?)~~")
MARK_RE = re.compile(r"==(.+?)==")
SPOILER_RE = re.compile(r"\|\|(.+?)\|\|")
MATH_INLINE_RE = re.compile(r"(?<!\\)\$([^$\n]+?)\$")


# --------------------------------------------------------------------------- inline
def inline(text: str) -> str:
    """Inline markdown -> rich HTML. Input is escaped first (no tag injection)."""
    text = escape(text or "")
    codes: list[str] = []

    def stash(match: re.Match[str]) -> str:
        codes.append(match.group(1))
        return f"\x00C{len(codes) - 1}\x00"

    text = INLINE_CODE_RE.sub(stash, text)
    text = LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = BOLD_RE.sub(r"<b>\1</b>", text)
    text = ITALIC_RE.sub(r"<i>\1</i>", text)
    text = STRIKE_RE.sub(r"<s>\1</s>", text)
    text = MARK_RE.sub(r"<mark>\1</mark>", text)
    text = SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", text)
    text = MATH_INLINE_RE.sub(r"<tg-math>\1</tg-math>", text)
    for idx, code in enumerate(codes):
        text = text.replace(f"\x00C{idx}\x00", f"<code>{code}</code>")
    return text


def _cells(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        i + 1 < len(lines)
        and "|" in lines[i]
        and TABLE_SEP_RE.match(lines[i + 1]) is not None
    )


def _block_start(lines: list[str], i: int) -> bool:
    line = lines[i]
    return bool(
        FENCE_RE.match(line)
        or HEADING_RE.match(line)
        or HR_RE.match(line)
        or QUOTE_RE.match(line)
        or TASK_RE.match(line)
        or UL_RE.match(line)
        or OL_RE.match(line)
        or MATH_BLOCK_RE.match(line)
        or _is_table_start(lines, i)
    )


def _table_html(header: list[str], rows: list[list[str]]) -> str:
    head = "<tr>" + "".join(f"<th>{inline(c)}</th>" for c in header) + "</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"<table bordered striped compact>{head}{body}</table>"


# --------------------------------------------------------------------------- blocks
def to_rich_html(text: str, limit: int = MAX_RICH_CHARS) -> str:
    text = strip_think(text or "").replace("\r\n", "\n")
    if not text.strip():
        return ""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        fence = FENCE_RE.match(line)
        if fence:
            lang = fence.group(1)
            i += 1
            buf: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # closing fence
            cls = f' class="language-{escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{escape(chr(10).join(buf))}</code></pre>")
            continue

        math = MATH_BLOCK_RE.match(line)
        if math:
            out.append(f"<tg-math-block>{escape(math.group(1).strip())}</tg-math-block>")
            i += 1
            continue

        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        if HR_RE.match(line):
            out.append("<hr/>")
            i += 1
            continue

        if _is_table_start(lines, i):
            header = _cells(lines[i])
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            out.append(_table_html(header, rows))
            continue

        if QUOTE_RE.match(line):
            buf = []
            while i < len(lines) and QUOTE_RE.match(lines[i]):
                buf.append(QUOTE_RE.match(lines[i]).group(1))  # type: ignore[union-attr]
                i += 1
            body = "<br>".join(inline(b) for b in buf if b.strip())
            out.append(f"<blockquote expandable>{body}</blockquote>")
            continue

        if TASK_RE.match(line):
            items = []
            while i < len(lines) and TASK_RE.match(lines[i]):
                m = TASK_RE.match(lines[i])  # type: ignore[assignment]
                checked = " checked" if m.group(1).lower() == "x" else ""
                items.append(f'<li><input type="checkbox"{checked}>{inline(m.group(2))}</li>')
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if UL_RE.match(line):
            items = []
            while i < len(lines) and UL_RE.match(lines[i]) and not TASK_RE.match(lines[i]):
                items.append(f"<li>{inline(UL_RE.match(lines[i]).group(1))}</li>")  # type: ignore[union-attr]
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if OL_RE.match(line):
            items = []
            while i < len(lines) and OL_RE.match(lines[i]):
                items.append(f"<li>{inline(OL_RE.match(lines[i]).group(1))}</li>")  # type: ignore[union-attr]
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue

        if not line.strip():
            i += 1
            continue

        buf = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not _block_start(lines, i):
            buf.append(lines[i])
            i += 1
        out.append("<p>" + "<br>".join(inline(b) for b in buf) + "</p>")

    result = "\n".join(out)
    if len(result) > limit:
        result = result[:limit] + "…"
    return result


# --------------------------------------------------------------------------- thinking
def thinking_details(reasoning: str, summary: str = "🧠 Thinking") -> str:
    body = to_rich_html(reasoning)
    if not body:
        return ""
    return f"<details><summary>{escape(summary)}</summary>{body}</details>"


def thinking_quote(reasoning: str, expandable: bool = True) -> str:
    body = "<br>".join(inline(line) for line in (reasoning or "").split("\n") if line.strip())
    if not body:
        return ""
    attr = " expandable" if expandable else ""
    return f"<blockquote{attr}>🧠 {body}</blockquote>"


def activity_blockquote(items: list[dict], summary: str = "", include_output: bool = False) -> str:
    """Compact collapsible tool log using <blockquote expandable>."""
    if not items:
        return ""
    rows: list[str] = []
    total_ms = 0
    for item in items[:8]:
        name = escape(str(item.get("name", "tool")))
        args = item.get("args") or {}
        ms = int(item.get("ms") or 0)
        total_ms += ms
        duration = f" · {ms / 1000:.1f}s" if ms else ""
        ok = "✓" if item.get("ok", True) else "✗"
        rows.append(f"{name} {ok}{duration}")
        if include_output:
            command = args.get("command") or args.get("code") or args.get("path") or args.get("query") or ""
            language = "bash" if item.get("name") == "run_shell" else (
                "python" if item.get("name") == "run_python" else "text"
            )
            output = escape(str(item.get("output", ""))[:300])
            if command:
                rows.append(f'<pre><code class="language-{language}">{escape(str(command))[:500]}</code></pre>')
            if output:
                rows.append(f"<pre>{output}</pre>")
    total = f" · {total_ms / 1000:.1f}s" if total_ms else ""
    label = summary or (
        f"⚙️ Code execution · {len(items)} tool call{'s' if len(items) != 1 else ''}{total}"
    )
    return f"<blockquote expandable>{escape(label)}\n" + "\n".join(rows) + "</blockquote>"


def activity_details(items: list[dict], summary: str = "") -> str:
    """Collapsible tool-execution block: command + real output, like thinking."""
    if not items:
        return ""
    rows: list[str] = []
    total_ms = 0
    for item in items[:8]:
        name = escape(str(item.get("name", "tool")))
        args = item.get("args") or {}
        command = (
            args.get("command")
            or args.get("code")
            or args.get("path")
            or args.get("query")
            or ""
        )
        language = "bash" if item.get("name") == "run_shell" else (
            "python" if item.get("name") == "run_python" else "text"
        )
        output = escape(str(item.get("output", ""))[:1500])
        ms = int(item.get("ms") or 0)
        total_ms += ms
        duration = f" · {ms / 1000:.1f}s" if ms else ""
        ok = "✓" if item.get("ok", True) else "✗"
        block = f"<p><b>{name}</b> {ok}{duration}</p>"
        if command:
            block += f'<pre><code class="language-{language}">{escape(str(command))[:1200]}</code></pre>'
        if output:
            block += f"<pre>{output}</pre>"
        rows.append(block)
    total = f" · {total_ms / 1000:.1f}s" if total_ms else ""
    label = summary or (
        f"⚙️ Code execution · {len(items)} tool call{'s' if len(items) != 1 else ''}{total}"
    )
    return f"<details><summary>{escape(label)}</summary>{''.join(rows)}</details>"
