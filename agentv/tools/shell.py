from __future__ import annotations

import re

from .base import Tool, ToolContext, ToolResult

# Never allowed, any role, any chat.
DANGEROUS = [
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\bchmod\s+-R\s+777\s+/\s*$",
    r"/etc/passwd",
    r"/etc/shadow",
    r"/proc/\d+/(?:mem|environ)",
    r"\b(?:cat|less|head|tail)\s+[^|;]*\.ssh/",
    r"\bid_rsa\b",
    r"\bdocker\s+(?:rm|kill|system|volume\s+rm)",
]

# Owner in a group: no destructive/system-level operations.
GROUP_DENY = [r"\brm\b", r"\bmv\b", r"\bdd\b", r"\bshred\b", r"\bmkfs\b", r"\bsystemctl\b", r"\bpkill\b"]

SHELL_PARAMS = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "Bash command to run inside the sandbox workspace."},
        "timeout": {"type": "integer", "description": "Seconds before the process is killed (max 600)."},
        "network": {"type": "boolean", "description": "Allow outbound network for this command (owner only)."},
    },
    "required": ["command"],
}


def _blocked(cmd: str) -> str | None:
    lowered = cmd.lower()
    for pattern in DANGEROUS:
        if re.search(pattern, lowered):
            return f"SECURITY BLOCK: pattern `{pattern}` is prohibited."
    return None


async def _run_shell(ctx: ToolContext, command: str = "", timeout: int | None = None, network: bool | None = None) -> ToolResult:
    command = (command or "").strip()
    if not command:
        return ToolResult("command is required", is_error=True)

    is_owner = ctx.role == "owner"
    if not is_owner and not ctx.cfg.allow_user_shell:
        return ToolResult("Shell access is restricted to the owner.", is_error=True)

    blocked = _blocked(command)
    if blocked:
        return ToolResult(blocked, is_error=True)

    if not is_owner:
        for pattern in (r"\bsudo\b", r"\bsu\b", r"\bchroot\b", r"\bmount\b", r"\bdocker\b", r"\bnsenter\b", r"/workspace", r"\.\./"):
            if re.search(pattern, command.lower()):
                return ToolResult("SECURITY BLOCK: reserved for the owner.", is_error=True)

    if not is_owner or not ctx.is_private:
        for pattern in GROUP_DENY:
            if re.search(pattern, command.lower()):
                return ToolResult(
                    "SECURITY BLOCK: destructive/system commands are only allowed in the owner's private chat.",
                    is_error=True,
                )

    use_net = bool(network) if network is not None else True
    if not is_owner:
        use_net = False
    timeout = min(max(int(timeout or ctx.cfg.tool_timeout_seconds), 5), int(ctx.cfg.tool_timeout_seconds))

    if is_owner:
        res = await ctx.sandbox.exec(ctx.ws_id, command, timeout=timeout, network=use_net)
    else:
        res = await ctx.sandbox.exec_user(ctx.ws_id, command, timeout=timeout)
    prefix = "" if res.exit_code == 0 else f"[exit {res.exit_code}] "
    return ToolResult(prefix + res.combined(), is_error=res.exit_code not in (0,), display=res.combined(500))


async def _run_python(ctx: ToolContext, code: str = "", timeout: int | None = None) -> ToolResult:
    if not (code or "").strip():
        return ToolResult("code is required", is_error=True)
    if ctx.role != "owner" and not ctx.cfg.allow_user_shell:
        return ToolResult("Python execution is restricted to the owner.", is_error=True)
    await ctx.sandbox.write_file(ctx.ws_id, ".agentv_run.py", code.encode("utf-8"))
    return await _run_shell(ctx, "python3 .agentv_run.py", timeout=timeout, network=False)


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="run_shell",
            description=(
                "Execute a bash command inside the isolated sandbox container. The working directory is "
                "the per-chat workspace. Use it for files, python, node, git, ffmpeg, package installs. "
                "Returns stdout/stderr/exit code."
            ),
            parameters=SHELL_PARAMS,
            handler=_run_shell,
            min_role="user",
            private_only=True,
        ),
        Tool(
            name="run_python",
            description="Write Python code to a file in the sandbox and execute it with python3.",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source code."},
                    "timeout": {"type": "integer"},
                },
                "required": ["code"],
            },
            handler=_run_python,
            min_role="user",
            private_only=True,
        ),
    ]
