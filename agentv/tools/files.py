from __future__ import annotations

import posixpath

from .base import Tool, ToolContext, ToolResult

MAX_READ = 200_000


def _clean_path(path: str) -> str:
    path = (path or ".").strip()
    norm = posixpath.normpath(path.replace("\\", "/"))
    if norm.startswith("/") or norm.startswith(".."):
        raise ValueError("path must be relative to the chat workspace")
    return norm


async def _read_file(ctx: ToolContext, path: str = "", max_bytes: int = MAX_READ) -> ToolResult:
    try:
        rel = _clean_path(path)
        data = await ctx.sandbox.read_file(ctx.ws_id, rel)
    except FileNotFoundError:
        return ToolResult(f"File not found: {path}", is_error=True)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Could not read {path}: {exc}", is_error=True)
    limit = min(max(int(max_bytes or MAX_READ), 1000), MAX_READ)
    text = data[:limit].decode("utf-8", "replace")
    if len(data) > limit:
        text += f"\n…[truncated {len(data) - limit} bytes]"
    return ToolResult(text or "(empty file)")


async def _write_file(ctx: ToolContext, path: str = "", content: str = "") -> ToolResult:
    try:
        rel = _clean_path(path)
        await ctx.sandbox.write_file(ctx.ws_id, rel, (content or "").encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Could not write {path}: {exc}", is_error=True)
    return ToolResult(f"Wrote {len((content or '').encode('utf-8'))} bytes to {rel}")


async def _list_dir(ctx: ToolContext, path: str = ".") -> ToolResult:
    try:
        rel = _clean_path(path)
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    res = await ctx.sandbox.exec(ctx.ws_id, f"ls -la --time-style=long-iso {rel!r}", timeout=20, network=False)
    return ToolResult(res.combined(8000), is_error=res.exit_code != 0)


async def _send_file(ctx: ToolContext, path: str = "", caption: str = "") -> ToolResult:
    try:
        rel = _clean_path(path)
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    export = getattr(ctx.sandbox, "export_file", None)
    try:
        local = await export(ctx.ws_id, rel) if export else ctx.sandbox.export_path(ctx.ws_id, rel)
    except FileNotFoundError:
        return ToolResult(f"File not found: {path}", is_error=True)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Could not export {path}: {exc}", is_error=True)
    if not local.exists():
        return ToolResult(f"File not found: {path}", is_error=True)
    limit = int(getattr(ctx.cfg, "max_download_mb", 0) or 0) * 1024 * 1024
    if limit and local.stat().st_size > limit:
        return ToolResult(
            f"File is {local.stat().st_size / 1024 / 1024:.1f} MB — download limit is "
            f"{ctx.cfg.max_download_mb} MB.",
            is_error=True,
        )
    return ToolResult(f"Attached {local.name}", files=[local])


async def _delete_file(ctx: ToolContext, path: str = "") -> ToolResult:
    if ctx.role != "owner":
        return ToolResult("Only the owner can delete files.", is_error=True)
    try:
        rel = _clean_path(path)
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    res = await ctx.sandbox.exec(ctx.ws_id, f"rm -f -- {rel!r}", timeout=20, network=False)
    return ToolResult(res.combined(2000), is_error=res.exit_code != 0)


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description="Read a UTF-8 text file from the chat workspace (max 200 KB).",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "max_bytes": {"type": "integer"}},
                "required": ["path"],
            },
            handler=_read_file,
            min_role="user",
        ),
        Tool(
            name="write_file",
            description="Create or overwrite a text file in the chat workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            handler=_write_file,
            min_role="user",
        ),
        Tool(
            name="list_dir",
            description="List files in a workspace directory.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            handler=_list_dir,
            min_role="user",
        ),
        Tool(
            name="send_file",
            description="Send a workspace file to the Telegram chat as an attachment.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "caption": {"type": "string"}},
                "required": ["path"],
            },
            handler=_send_file,
            min_role="user",
        ),
        Tool(
            name="delete_file",
            description="Delete a file in the chat workspace (owner only).",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=_delete_file,
            min_role="owner",
        ),
    ]
