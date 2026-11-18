"""Real headless browser: read, screenshot, click, fill."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult
from ..browser import get_browser


async def _browser(
    ctx: ToolContext,
    action: str = "read",
    url: str = "",
    selector: str = "",
    text: str = "",
    full_page: bool = False,
) -> ToolResult:
    if not (url or "").startswith(("http://", "https://")):
        return ToolResult("http(s) url is required", is_error=True)
    action = (action or "read").lower()
    bm = get_browser()
    try:
        if action == "read":
            content = await bm.read(url, selector)
            return ToolResult(content[:12000] or "(empty page)")
        if action == "screenshot":
            # always viewport-sized 1920x1080 (no full-page capture)
            path = await bm.screenshot(url, full_page=False)
            responder = ctx.responder
            if responder is not None and hasattr(responder, "send_photo"):
                await responder.send_photo(path)
                return ToolResult("Screenshot sent.")
            return ToolResult(f"Screenshot saved: {path}", files=[path])
        if action == "click":
            if not selector:
                return ToolResult("selector is required for click", is_error=True)
            return ToolResult((await bm.click(url, selector))[:12000])
        if action == "fill":
            if not selector or not text:
                return ToolResult("selector and text are required for fill", is_error=True)
            return ToolResult((await bm.fill(url, selector, text, submit=True))[:12000])
        return ToolResult("action must be read|screenshot|click|fill", is_error=True)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"browser failed: {exc}", is_error=True)


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="browser",
            description=(
                "Real headless browser: action=read|screenshot|click|fill. "
                "Use for JS-heavy pages, screenshots, and interactions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "screenshot", "click", "fill"]},
                    "url": {"type": "string"},
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["action", "url"],
            },
            handler=_browser,
            min_role="user",
        )
    ]
