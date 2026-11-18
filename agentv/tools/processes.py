"""Background process control inside the sandbox."""

from __future__ import annotations

import shlex

from .base import Tool, ToolContext, ToolResult


async def _process(ctx: ToolContext, action: str = "list", command: str = "", pid: int = 0) -> ToolResult:
    action = (action or "list").lower()
    if action == "start":
        if not command.strip():
            return ToolResult("command is required", is_error=True)
        safe = shlex.quote(command)
        cmd = (
            "mkdir -p .agentv/proc && "
            f"nohup bash -lc {safe} > .agentv/proc/proc_$(date +%s).log 2>&1 & echo started:$!"
        )
        res = await ctx.sandbox.exec(ctx.ws_id, cmd, timeout=10, network=True)
        return ToolResult(res.combined(2000), is_error=res.exit_code != 0)
    if action in ("kill", "stop"):
        if not pid:
            return ToolResult("pid is required", is_error=True)
        res = await ctx.sandbox.exec(ctx.ws_id, f"kill -TERM {int(pid)} 2>&1 || true", timeout=10, network=False)
        return ToolResult(f"sent TERM to {pid}\n{res.combined(500)}")
    if action == "logs":
        res = await ctx.sandbox.exec(
            ctx.ws_id, "ls -t .agentv/proc/*.log 2>/dev/null | head -1 | xargs -r tail -n 80", timeout=10, network=False
        )
        return ToolResult(res.combined(8000))
    res = await ctx.sandbox.exec(ctx.ws_id, "ps -eo pid,etime,cmd --sort=start_time | tail -n 25", timeout=10, network=False)
    return ToolResult(res.combined(8000))


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="process_manage",
            description="Start/list/kill background processes in the workspace, and show recent logs.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["start", "list", "kill", "logs"]},
                    "command": {"type": "string"},
                    "pid": {"type": "integer"},
                },
                "required": ["action"],
            },
            handler=_process,
            min_role="user",
        )
    ]
