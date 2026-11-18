from __future__ import annotations

from . import (
    browser_tool,
    delegate,
    files,
    groups,
    history,
    media,
    media_extra,
    meme,
    memory,
    processes,
    reminders,
    schedule,
    settings,
    shell,
    skills,
    team,
    telegram,
    todos,
    web,
    workspace,
)
from .base import Tool, ToolContext, ToolRegistry, ToolResult

__all__ = ["Tool", "ToolContext", "ToolRegistry", "ToolResult", "build_registry"]


def build_registry(db=None) -> ToolRegistry:
    registry = ToolRegistry(db=db)
    for module in (
        shell, files, web, memory, telegram, skills, reminders, settings, media, groups,
        workspace, todos, history, processes, schedule, media_extra, delegate, browser_tool, meme, team,
    ):
        registry.register_all(module.build_tools())
    return registry
