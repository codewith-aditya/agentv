"""Recurring scheduled prompts."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult

INTERVALS = {"15m": 900, "30m": 1800, "1h": 3600, "6h": 21600, "12h": 43200, "1d": 86400}


def _parse_interval(value: str) -> int | None:
    v = (value or "").strip().lower()
    if v in INTERVALS:
        return INTERVALS[v]
    if v.endswith("m") and v[:-1].isdigit():
        return max(60, int(v[:-1]) * 60)
    if v.endswith("h") and v[:-1].isdigit():
        return int(v[:-1]) * 3600
    if v.endswith("d") and v[:-1].isdigit():
        return int(v[:-1]) * 86400
    if v.isdigit():
        return max(60, int(v))
    return None


async def _schedule(ctx: ToolContext, action: str = "list", prompt: str = "", every: str = "", schedule_id: int = 0) -> ToolResult:
    action = (action or "list").lower()
    if action == "add":
        if not prompt.strip():
            return ToolResult("prompt is required", is_error=True)
        seconds = _parse_interval(every)
        if not seconds:
            return ToolResult("every must be like 15m, 1h, 6h, 1d", is_error=True)
        sid = await ctx.db.add_schedule(ctx.chat_id, ctx.transport, prompt.strip(), seconds, ctx.user_id)
        return ToolResult(f"⏰ schedule #{sid} every {every} — agent will run: {prompt.strip()}")
    if action in ("del", "delete", "cancel"):
        if not schedule_id:
            return ToolResult("schedule_id is required", is_error=True)
        ok = await ctx.db.delete_schedule(ctx.chat_id, schedule_id)
        return ToolResult(f"deleted schedule #{schedule_id}" if ok else "schedule not found", is_error=not ok)
    rows = await ctx.db.list_schedules(ctx.chat_id)
    if not rows:
        return ToolResult("No recurring schedules.")
    return ToolResult(
        "\n".join(f"#{r['id']} every {r['interval_seconds']}s — {r['prompt']}" for r in rows)
    )


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="schedule_task",
            description="Create/list/delete recurring tasks: action=add|list|del, every=15m|1h|1d.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list", "del"]},
                    "prompt": {"type": "string"},
                    "every": {"type": "string"},
                    "schedule_id": {"type": "integer"},
                },
                "required": ["action"],
            },
            handler=_schedule,
            min_role="user",
        )
    ]
