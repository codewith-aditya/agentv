"""Workspace tools: search, targeted edit, PDF text extraction."""

from __future__ import annotations

import posixpath

from .base import Tool, ToolContext, ToolResult


def _clean(path: str) -> str:
    p = posixpath.normpath((path or ".").replace("\\", "/"))
    if p.startswith("/") or p.startswith(".."):
        raise ValueError("path must be relative to the workspace")
    return p


async def _search_files(ctx: ToolContext, pattern: str = "", path: str = ".", glob: str = "") -> ToolResult:
    if not pattern:
        return ToolResult("pattern is required", is_error=True)
    try:
        rel = _clean(path)
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    rg = f"rg -n --hidden --no-ignore -g {glob!r}" if glob else "rg -n --hidden --no-ignore"
    cmd = f"{rg} -e {pattern!r} {rel} 2>/dev/null | head -n 120"
    res = await ctx.sandbox.exec(ctx.ws_id, cmd, timeout=20, network=False)
    return ToolResult(res.combined(12000), is_error=bool(res.stderr and not res.stdout))


async def _edit_file(
    ctx: ToolContext, path: str = "", old: str = "", new: str = "", replace_all: bool = False
) -> ToolResult:
    if not path or not old:
        return ToolResult("path and old text are required", is_error=True)
    try:
        rel = _clean(path)
        data = (await ctx.sandbox.read_file(ctx.ws_id, rel)).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"could not read {path}: {exc}", is_error=True)
    count = data.count(old)
    if count == 0:
        return ToolResult("old text not found in file", is_error=True)
    updated = data.replace(old, new, -1 if replace_all else 1)
    await ctx.sandbox.write_file(ctx.ws_id, rel, updated.encode("utf-8"))
    return ToolResult(f"edited {rel}: {count if replace_all else 1} replacement(s)")


async def _pdf_extract(ctx: ToolContext, path: str = "", max_chars: int = 8000) -> ToolResult:
    try:
        rel = _clean(path)
        data = await ctx.sandbox.read_file(ctx.ws_id, rel)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"could not read {path}: {exc}", is_error=True)
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:50]).strip()
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"PDF parse failed: {exc}", is_error=True)
    limit = min(max(int(max_chars or 8000), 500), 30000)
    return ToolResult((text or "(no text layer)")[:limit])


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="search_files",
            description="Search text inside workspace files with ripgrep (pattern + optional glob).",
            parameters={
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}},
                "required": ["pattern"],
            },
            handler=_search_files,
            min_role="user",
        ),
        Tool(
            name="edit_file",
            description="Targeted text replacement in a workspace file (old -> new), optionally all occurrences.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old", "new"],
            },
            handler=_edit_file,
            min_role="user",
        ),
        Tool(
            name="pdf_extract",
            description="Extract text from a PDF in the workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
                "required": ["path"],
            },
            handler=_pdf_extract,
            min_role="user",
        ),
    ]
