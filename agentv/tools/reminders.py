"""Reminders: parse natural when values, store them, list/cancel."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .base import Tool, ToolContext, ToolResult

REL_RE = re.compile(r"^(?:(\d+)\s*w)?\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?$")
CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_when(raw: str) -> datetime | None:
    """Parse '10m', '1h30m', 'in 2h', '14:30', '2026-11-19 09:00' → aware datetime."""
    now = datetime.now().astimezone()
    text = (raw or "").strip().lower()
    text = re.sub(r"^in\s+", "", text)
    if not text:
        return None

    clock = CLOCK_RE.match(text)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    rel = REL_RE.match(text)
    if rel and any(rel.groups()):
        weeks, days, hours, minutes, seconds = (int(g or 0) for g in rel.groups())
        delta = timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
        if delta.total_seconds() <= 0:
            return None
        return now + delta

    # ISO-ish: 2026-11-19 09:00 or 2026-11-19T09:00
    try:
        parsed = datetime.fromisoformat(text.replace("z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed if parsed > now else None
    except ValueError:
        return None


def _fmt(dt: datetime) -> str:
    return dt.strftime("%a %d %b · %H:%M")


async def _set_reminder(ctx: ToolContext, text: str = "", when: str = "") -> ToolResult:
    text = (text or "").strip()
    if not text:
        return ToolResult("text is required", is_error=True)
    due = parse_when(when)
    if due is None:
        return ToolResult(
            "could not parse `when`. Use e.g. `10m`, `1h30m`, `14:30`, `2026-11-19 09:00`.",
            is_error=True,
        )
    rid = await ctx.db.add_reminder(ctx.chat_id, ctx.transport, ctx.user_id, text, int(due.timestamp()))
    return ToolResult(f"⏰ Reminder #{rid} set for {_fmt(due)} — {text}")


async def _list_reminders(ctx: ToolContext, include_done: bool = False) -> ToolResult:
    rows = await ctx.db.list_reminders(ctx.chat_id, include_done=bool(include_done), limit=50)
    if not rows:
        return ToolResult("No pending reminders in this chat.")
    lines = []
    for row in rows:
        due = datetime.fromtimestamp(int(row["due_ts"])).astimezone()
        mark = "✅" if row["sent"] else "⏰"
        lines.append(f"{mark} #{row['id']} · {_fmt(due)} · {row['text']}")
    return ToolResult("\n".join(lines))


async def _cancel_reminder(ctx: ToolContext, reminder_id: int = 0) -> ToolResult:
    if not reminder_id:
        return ToolResult("reminder_id is required", is_error=True)
    ok = await ctx.db.delete_reminder(int(reminder_id), ctx.chat_id)
    return ToolResult(f"🗑 reminder #{reminder_id} cancelled." if ok else "reminder not found in this chat.", is_error=not ok)


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="set_reminder",
            description="Schedule a reminder message for this chat. `when` accepts 10m, 1h30m, 14:30, or an ISO datetime.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "what to remind about"},
                    "when": {"type": "string", "description": "10m | 1h30m | 14:30 | 2026-11-19 09:00"},
                },
                "required": ["text", "when"],
            },
            handler=_set_reminder,
            min_role="user",
        ),
        Tool(
            name="list_reminders",
            description="List scheduled reminders for this chat.",
            parameters={"type": "object", "properties": {"include_done": {"type": "boolean"}}},
            handler=_list_reminders,
            min_role="user",
        ),
        Tool(
            name="cancel_reminder",
            description="Cancel a reminder by id in this chat.",
            parameters={
                "type": "object",
                "properties": {"reminder_id": {"type": "integer"}},
                "required": ["reminder_id"],
            },
            handler=_cancel_reminder,
            min_role="user",
        ),
    ]
