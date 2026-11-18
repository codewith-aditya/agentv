"""Team brain: decisions, follow-ups and facts for a group."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult


async def _team_brain(
    ctx: ToolContext, action: str = "list", kind: str = "decision", content: str = "", query: str = ""
) -> ToolResult:
    action = (action or "list").lower()
    if action == "add":
        if not content.strip():
            return ToolResult("content is required", is_error=True)
        kind = kind if kind in ("decision", "todo", "fact") else "decision"
        nid = await ctx.db.add_team_note(ctx.chat_id, kind, content.strip(), ctx.user_id)
        return ToolResult(f"🧠 saved {kind} #{nid}: {content.strip()}")
    if action == "search":
        if not query.strip():
            return ToolResult("query is required", is_error=True)
        rows = await ctx.db.search_team_notes(ctx.chat_id, query.strip())
    else:
        rows = await ctx.db.list_team_notes(ctx.chat_id)
    if not rows:
        return ToolResult("Team brain is empty for this group.")
    lines = [f"[{r['kind']}] #{r['id']} {r['content']} ({r['created_at'][:16]})" for r in reversed(rows)]
    return ToolResult("\n".join(lines))


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="team_brain",
            description="Group memory: record/list/search decisions, follow-ups and facts for this group.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list", "search"]},
                    "kind": {"type": "string", "enum": ["decision", "todo", "fact"]},
                    "content": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["action"],
            },
            handler=_team_brain,
            min_role="user",
            group_only=True,
        )
    ]
