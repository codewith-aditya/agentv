from __future__ import annotations

from .base import Tool, ToolContext, ToolResult


async def _remember(ctx: ToolContext, content: str = "", scope: str = "chat") -> ToolResult:
    content = (content or "").strip()
    if not content:
        return ToolResult("content is required", is_error=True)
    scope = scope if scope in ("chat", "global", "user") else "chat"
    if scope == "global" and ctx.role != "owner":
        scope = "chat"
    if scope == "user":
        mem_id = await ctx.db.add_user_memory(ctx.user_id, content)
        return ToolResult(f"Saved user memory #{mem_id}: {content}")
    chat_id = str(ctx.chat_id) if scope == "chat" else None
    mem_id = await ctx.db.add_memory(chat_id, content, scope=scope)
    return ToolResult(f"Saved memory #{mem_id} ({scope}): {content}")


async def _list_memories(ctx: ToolContext, limit: int = 20) -> ToolResult:
    rows = await ctx.db.list_memories(str(ctx.chat_id), limit=min(max(int(limit or 20), 1), 100))
    user_rows = await ctx.db.list_user_memories(ctx.user_id, limit=20) if ctx.is_private else []
    if not rows and not user_rows:
        return ToolResult("No memories saved yet.")
    lines = [f"#{r['id']} [{r['scope']}] {r['content']}" for r in rows]
    lines += [f"u#{r['id']} [user] {r['content']}" for r in user_rows]
    return ToolResult("\n".join(lines))


async def _forget(ctx: ToolContext, memory_id: int = 0) -> ToolResult:
    if not memory_id:
        return ToolResult("memory_id is required", is_error=True)
    await ctx.db.delete_memory(int(memory_id))
    return ToolResult(f"Deleted memory #{memory_id}")


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="remember",
            description="Persist a durable fact/instruction. scope=global is owner-only, scope=chat is per-chat.",
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "scope": {"type": "string", "enum": ["chat", "global", "user"]},
                },
                "required": ["content"],
            },
            handler=_remember,
            min_role="user",
        ),
        Tool(
            name="memory_list",
            description="List saved memories visible in this chat.",
            parameters={"type": "object", "properties": {"limit": {"type": "integer"}}},
            handler=_list_memories,
            min_role="user",
        ),
        Tool(
            name="forget",
            description="Delete a saved memory by id.",
            parameters={"type": "object", "properties": {"memory_id": {"type": "integer"}}, "required": ["memory_id"]},
            handler=_forget,
            min_role="admin",
        ),
    ]
