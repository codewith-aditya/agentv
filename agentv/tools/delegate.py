"""Delegate a focused research answer to a separate model call."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult


async def _delegate(ctx: ToolContext, prompt: str = "", system: str = "") -> ToolResult:
    if not prompt.strip():
        return ToolResult("prompt is required", is_error=True)
    if ctx.llm is None or ctx.provider is None:
        return ToolResult("No LLM available for delegation.", is_error=True)
    try:
        result = await ctx.llm.chat(
            ctx.provider,
            [
                {"role": "system", "content": system or "You are a focused research sub-agent. Answer precisely and concisely."},
                {"role": "user", "content": prompt[:12000]},
            ],
            model=ctx.provider.model,
            temperature=0.3,
        )
        return ToolResult(result.content or "(no answer)")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"delegate failed: {exc}", is_error=True)


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="delegate_task",
            description="Ask a separate model call to solve a focused sub-task and return its answer.",
            parameters={
                "type": "object",
                "properties": {"prompt": {"type": "string"}, "system": {"type": "string"}},
                "required": ["prompt"],
            },
            handler=_delegate,
            min_role="user",
        )
    ]
