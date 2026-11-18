"""Conversation memory search (messages stored in SQLite)."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult


async def _session_search(ctx: ToolContext, query: str = "", limit: int = 10) -> ToolResult:
    if not (query or "").strip():
        return ToolResult("query is required", is_error=True)
    rows = await ctx.db.search_messages(ctx.chat_id, query.strip(), min(max(int(limit or 10), 1), 50))
    if not rows:
        return ToolResult("No matching messages in this chat.")
    lines = [f"[{r['created_at']}] {r['role']}: {r['content'][:300]}" for r in reversed(rows)]
    return ToolResult("\n".join(lines))


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="session_search",
            description="Search earlier messages in this chat for a keyword/phrase.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
            handler=_session_search,
            min_role="user",
        )
    ]
