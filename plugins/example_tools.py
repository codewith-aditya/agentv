"""Example Agent V plugin.

Everything here is optional — drop a .py file in plugins/ and it is loaded on
startup (or `/plugins reload`). Keep credentials in env, not in the file.
"""

from __future__ import annotations

import os
import platform
import time

from agentv.tools.base import Tool, ToolContext, ToolResult

__version__ = "1.0.0"

_START = time.time()


async def _sysinfo(ctx: ToolContext) -> ToolResult:
    """Report the runtime the bot sees (host, not the sandbox)."""
    return ToolResult(
        f"agentv host: {platform.system()} {platform.release()}\n"
        f"python: {platform.python_version()}\n"
        f"pid: {os.getpid()}\n"
        f"uptime: {int(time.time() - _START)}s\n"
        f"workspace backend: {ctx.sandbox.health()}"
    )


async def _status_command(ctx) -> None:
    await ctx.reply(f"⏱ bot process uptime: {int(time.time() - _START)}s")


def setup(api) -> None:
    api.register_tool(
        Tool(
            name="host_sysinfo",
            description="Report the host/runtime information of the bot process.",
            parameters={"type": "object", "properties": {}},
            handler=_sysinfo,
            min_role="owner",
            source="plugin:example_tools",
        )
    )
    api.add_command("uptime", "Bot process uptime", _status_command)
    api.log.info("example plugin ready")
