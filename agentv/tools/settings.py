"""Per-chat settings the model can change on request."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult

LEVELS = ("instant", "low", "medium", "high", "max")


async def _set_reasoning(ctx: ToolContext, level: str = "") -> ToolResult:
    level = (level or "").strip().lower()
    if level not in LEVELS:
        return ToolResult("level must be one of: instant, low, medium, high, max", is_error=True)
    await ctx.db.chat_set(str(ctx.chat_id), "reasoning", level)
    detail = {
        "instant": "no reasoning, fastest",
        "low": "brief reasoning",
        "medium": "balanced reasoning",
        "high": "careful reasoning",
        "max": "maximum reasoning + verification",
    }[level]
    return ToolResult(f"reasoning set to {level} for this chat only ({detail}).")


async def _set_stream(ctx: ToolContext, enabled: bool = True) -> ToolResult:
    await ctx.db.chat_set(str(ctx.chat_id), "stream", "on" if enabled else "off")
    return ToolResult(f"live streaming {'enabled' if enabled else 'disabled'} for this chat.")


async def _chat_settings(ctx: ToolContext) -> ToolResult:
    reasoning = await ctx.db.chat_get(str(ctx.chat_id), "reasoning") or ctx.cfg.reasoning_default
    stream = await ctx.db.chat_get(str(ctx.chat_id), "stream") or "on"
    provider = ctx.provider.name if ctx.provider else "?"
    model = ctx.provider.model if ctx.provider else "?"
    return ToolResult(f"reasoning={reasoning} · stream={stream} · provider={provider} · model={model}")


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="set_reasoning",
            description="Change the reasoning/thinking level for this chat: instant (no thinking), low, medium, high, max.",
            parameters={
                "type": "object",
                "properties": {"level": {"type": "string", "enum": list(LEVELS)}},
                "required": ["level"],
            },
            handler=_set_reasoning,
            min_role="user",
        ),
        Tool(
            name="set_stream",
            description="Enable or disable live streaming/thinking UI for this chat.",
            parameters={"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
            handler=_set_stream,
            min_role="user",
        ),
        Tool(
            name="chat_settings",
            description="Show the current reasoning level, streaming state, provider and model for this chat.",
            parameters={"type": "object", "properties": {}},
            handler=_chat_settings,
            min_role="user",
        ),
    ]
