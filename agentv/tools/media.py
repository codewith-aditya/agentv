"""Send stickers / GIFs back to the chat."""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolContext, ToolResult


async def _export(ctx: ToolContext, path: str) -> Path | None:
    export = getattr(ctx.sandbox, "export_file", None)
    try:
        local = await export(ctx.ws_id, path) if export else ctx.sandbox.export_path(ctx.ws_id, path)
    except Exception:  # noqa: BLE001
        return None
    return local if Path(local).exists() else None


async def _send_sticker(ctx: ToolContext, key: str = "", file_id: str = "", path: str = "") -> ToolResult:
    responder = ctx.responder
    if responder is None or not hasattr(responder, "send_sticker"):
        return ToolResult("Stickers are not supported on this transport.", is_error=True)
    try:
        if not file_id and key:
            file_id = await ctx.db.get_sticker_file(key, "sticker") or await ctx.db.get_sticker_file(key)
        if file_id:
            await responder.send_sticker(file_id=file_id)
            return ToolResult("Sticker sent.")
        if not path:
            return ToolResult(
                "Provide a cached `key` (e.g. 😂, doge), a sticker `file_id`, or a workspace `path`.",
                is_error=True,
            )
        local = await _export(ctx, path)
        if local is None:
            return ToolResult(f"Sticker file not found: {path}", is_error=True)
        await responder.send_sticker(path=local)
        return ToolResult(f"Sticker sent: {local.name}")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Sticker send failed: {exc}", is_error=True)


async def _send_gif(ctx: ToolContext, key: str = "", path: str = "", caption: str = "") -> ToolResult:
    responder = ctx.responder
    if responder is None or not hasattr(responder, "send_animation"):
        return ToolResult("GIF/animation is not supported on this transport.", is_error=True)
    try:
        if key:
            file_id = await ctx.db.get_sticker_file(key, "gif")
            if file_id:
                responder_any = responder
                if hasattr(responder_any, "send_animation_id"):
                    await responder_any.send_animation_id(file_id)
                else:
                    return ToolResult("Cached GIF send is not supported.", is_error=True)
                return ToolResult("GIF sent.")
    except Exception:  # noqa: BLE001
        pass
    if not path:
        return ToolResult("path is required (.mp4/.gif)", is_error=True)
    local = await _export(ctx, path)
    if local is None:
        return ToolResult(f"File not found: {path}", is_error=True)
    try:
        await responder.send_animation(local, caption=caption)
        return ToolResult(f"Animation sent: {local.name}")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Animation send failed: {exc}", is_error=True)


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="send_sticker",
            description="Send a Telegram sticker: either an existing sticker file_id, or a .webp/.png/.tgs file from the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "cached sticker key (emoji or name)"},
                    "file_id": {"type": "string", "description": "existing Telegram sticker file_id"},
                    "path": {"type": "string", "description": "workspace path to a sticker file"},
                },
            },
            handler=_send_sticker,
            min_role="user",
        ),
        Tool(
            name="send_gif",
            description="Send a GIF: cached key, or a .mp4/.gif file from the workspace.",
            parameters={
                "type": "object",
                "properties": {"key": {"type": "string"}, "path": {"type": "string"}, "caption": {"type": "string"}},
            },
            handler=_send_gif,
            min_role="user",
        ),
    ]
