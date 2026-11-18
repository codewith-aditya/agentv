from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolResult


async def _lookup(ctx: ToolContext, target: str = "") -> ToolResult:
    if not ctx.tg:
        return ToolResult("Telegram lookup is not available on this transport.", is_error=True)
    return ToolResult(await ctx.tg.lookup(target))


async def _chat_stats(ctx: ToolContext) -> ToolResult:
    if not ctx.tg:
        return ToolResult("Chat stats are not available on this transport.", is_error=True)
    return ToolResult(await ctx.tg.chat_stats(ctx.chat_id))


async def _manage(ctx: ToolContext, action: str = "", user: str = "", minutes: int = 0) -> ToolResult:
    if not ctx.tg:
        return ToolResult("User management is not available on this transport.", is_error=True)
    action = (action or "").lower()
    if action not in ("kick", "ban", "unban", "mute", "unmute"):
        return ToolResult("action must be one of: kick, ban, unban, mute, unmute", is_error=True)
    if ctx.role not in ("owner", "admin"):
        return ToolResult("Admin rights required.", is_error=True)
    return ToolResult(await ctx.tg.manage(ctx.chat_id, user, action, int(minutes or 0) or None))


async def _delete_message(ctx: ToolContext, message_id: int = 0) -> ToolResult:
    if not ctx.tg:
        return ToolResult("Message deletion is not available on this transport.", is_error=True)
    return ToolResult(await ctx.tg.delete_message(ctx.chat_id, int(message_id or 0)))


async def _leave(ctx: ToolContext) -> ToolResult:
    if not ctx.tg:
        return ToolResult("Leaving is not available on this transport.", is_error=True)
    if ctx.role != "owner":
        return ToolResult("Only the owner can make the account leave a chat.", is_error=True)
    return ToolResult(await ctx.tg.leave(ctx.chat_id))


async def _react(ctx: ToolContext, emoji: str = "👍", message_id: int = 0) -> ToolResult:
    if not ctx.tg or not hasattr(ctx.tg, "react"):
        return ToolResult("Reactions are not supported on this transport.", is_error=True)
    target = int(message_id or ctx.message_id or 0)
    if not target:
        return ToolResult("No message to react to — pass message_id.", is_error=True)
    emoji = (emoji or "👍").strip()
    if len(emoji) > 8:
        return ToolResult("emoji must be a single reaction emoji (e.g. 👍 ❤️ 🔥).", is_error=True)
    return ToolResult(await ctx.tg.react(ctx.chat_id, target, emoji))


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="tg_lookup",
            description="Look up a Telegram user/group/channel by @username, link or numeric id.",
            parameters={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
            handler=_lookup,
            min_role="user",
        ),
        Tool(
            name="tg_chat_stats",
            description="Get member counts and metadata for the current chat.",
            parameters={"type": "object", "properties": {}},
            handler=_chat_stats,
            min_role="user",
            group_only=True,
        ),
        Tool(
            name="tg_manage_user",
            description="Kick/ban/unban/mute/unmute a member of the current group (admin+).",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["kick", "ban", "unban", "mute", "unmute"]},
                    "user": {"type": "string", "description": "numeric id or @username"},
                    "minutes": {"type": "integer", "description": "0 = permanent"},
                },
                "required": ["action", "user"],
            },
            handler=_manage,
            min_role="admin",
            group_only=True,
        ),
        Tool(
            name="tg_delete_message",
            description="Delete a message by id in the current chat.",
            parameters={"type": "object", "properties": {"message_id": {"type": "integer"}}, "required": ["message_id"]},
            handler=_delete_message,
            min_role="admin",
        ),
        Tool(
            name="tg_leave_chat",
            description="Leave the current group (owner only).",
            parameters={"type": "object", "properties": {}},
            handler=_leave,
            min_role="owner",
            group_only=True,
        ),
        Tool(
            name="react",
            description=(
                "React to a message with any emoji, chosen by mood: 👍 agree, ❤️ love, 🔥 impressive, "
                "😂 funny, 🤔 curious, 🎉 celebrate, 🙏 thanks, 👀 noticed. Defaults to the user's latest message."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "emoji": {"type": "string", "description": "reaction emoji"},
                    "message_id": {"type": "integer", "description": "target message id (optional)"},
                },
                "required": ["emoji"],
            },
            handler=_react,
            min_role="user",
        ),
    ]
