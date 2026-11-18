"""Per-chat todo list."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult


async def _todo(ctx: ToolContext, action: str = "list", text: str = "", todo_id: int = 0) -> ToolResult:
    action = (action or "list").lower()
    if action == "add":
        if not text.strip():
            return ToolResult("text is required", is_error=True)
        tid = await ctx.db.add_todo(ctx.chat_id, text.strip())
        return ToolResult(f"✅ added todo #{tid}: {text.strip()}")
    if action in ("done", "complete"):
        if not todo_id:
            return ToolResult("todo_id is required", is_error=True)
        ok = await ctx.db.complete_todo(ctx.chat_id, todo_id)
        return ToolResult(f"done #{todo_id}" if ok else "todo not found", is_error=not ok)
    if action == "clear":
        await ctx.db.clear_todos(ctx.chat_id)
        return ToolResult("cleared all todos")
    rows = await ctx.db.list_todos(ctx.chat_id)
    if not rows:
        return ToolResult("No open todos.")
    return ToolResult("\n".join(f"#{r['id']} [ ] {r['text']}" for r in rows))


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="todo_list",
            description="Manage this chat's todo list: action=add|list|done|clear.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list", "done", "clear"]},
                    "text": {"type": "string"},
                    "todo_id": {"type": "integer"},
                },
                "required": ["action"],
            },
            handler=_todo,
            min_role="user",
        )
    ]
