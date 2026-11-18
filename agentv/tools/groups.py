"""Group-only tools: stored chat history, search, per-user history, pinning."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult


def _fmt(rows, limit: int = 80) -> str:
    lines = []
    for row in reversed(rows[:limit]):
        lines.append(f"[{row['created_at'][11:16]}] {row['sender_name']} (#{row['message_id']}): {row['text']}")
    return "\n".join(lines)


async def _chat_history(ctx: ToolContext, limit: int = 100) -> ToolResult:
    rows = await ctx.db.group_history(ctx.chat_id, min(max(int(limit or 100), 1), 500))
    if not rows:
        return ToolResult("No stored messages yet for this group.")
    return ToolResult(_fmt(rows))


async def _search_chat(ctx: ToolContext, query: str = "", limit: int = 20) -> ToolResult:
    if not (query or "").strip():
        return ToolResult("query is required", is_error=True)
    rows = await ctx.db.search_group(ctx.chat_id, query.strip(), min(max(int(limit or 20), 1), 100))
    if not rows:
        return ToolResult(f"No messages matching '{query}'.")
    return ToolResult(_fmt(rows))


async def _user_history(ctx: ToolContext, user: str = "", limit: int = 30) -> ToolResult:
    if not (user or "").strip():
        return ToolResult("user (id or name) is required", is_error=True)
    rows = await ctx.db.group_user_history(ctx.chat_id, user.strip().lstrip("@"), min(max(int(limit or 30), 1), 200))
    if not rows:
        return ToolResult(f"No stored messages for '{user}'.")
    return ToolResult(_fmt(rows))


async def _pin_message(ctx: ToolContext, message_id: int = 0, notify: bool = False) -> ToolResult:
    if not ctx.tg or not hasattr(ctx.tg, "pin"):
        return ToolResult("Pinning is not supported on this transport.", is_error=True)
    target = int(message_id or ctx.message_id or 0)
    if not target:
        return ToolResult("message_id is required", is_error=True)
    return ToolResult(await ctx.tg.pin(ctx.chat_id, target, bool(notify)))


async def _unpin_message(ctx: ToolContext, message_id: int = 0) -> ToolResult:
    if not ctx.tg or not hasattr(ctx.tg, "unpin"):
        return ToolResult("Unpinning is not supported on this transport.", is_error=True)
    return ToolResult(await ctx.tg.unpin(ctx.chat_id, int(message_id or 0)))


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="chat_history",
            description="Read the stored message history of the current group (most recent last).",
            parameters={"type": "object", "properties": {"limit": {"type": "integer"}}},
            handler=_chat_history,
            min_role="user",
            group_only=True,
        ),
        Tool(
            name="search_chat",
            description="Search stored messages in the current group by text.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
            handler=_search_chat,
            min_role="user",
            group_only=True,
        ),
        Tool(
            name="user_history",
            description="Read stored messages from one user in the current group (name or numeric id).",
            parameters={
                "type": "object",
                "properties": {"user": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["user"],
            },
            handler=_user_history,
            min_role="user",
            group_only=True,
        ),
        Tool(
            name="pin_message",
            description="Pin a message in the current group (bot must be admin).",
            parameters={
                "type": "object",
                "properties": {"message_id": {"type": "integer"}, "notify": {"type": "boolean"}},
            },
            handler=_pin_message,
            min_role="admin",
            group_only=True,
        ),
        Tool(
            name="unpin_message",
            description="Unpin a message (or the pinned message) in the current group.",
            parameters={"type": "object", "properties": {"message_id": {"type": "integer"}}},
            handler=_unpin_message,
            min_role="admin",
            group_only=True,
        ),
    ]
