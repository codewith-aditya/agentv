"""Text to speech (edge-tts) and rendered web reading (r.jina.ai)."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx

from .base import Tool, ToolContext, ToolResult


async def _tts(ctx: ToolContext, text: str = "", voice: str = "en-US-AriaNeural") -> ToolResult:
    if not text.strip():
        return ToolResult("text is required", is_error=True)
    responder = ctx.responder
    if responder is None or not hasattr(responder, "send_voice"):
        return ToolResult("Voice replies are not supported on this transport.", is_error=True)
    try:
        import edge_tts

        name = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        tmp = Path("/tmp") / name
        await edge_tts.Communicate(text[:4000], voice).save(str(tmp))
        await responder.send_voice(tmp)
        try:
            tmp.unlink()
        except OSError:
            pass
        return ToolResult(f"Voice message sent ({len(text)} chars).")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"TTS failed: {exc}", is_error=True)


async def _browser_read(ctx: ToolContext, url: str = "", max_chars: int = 8000) -> ToolResult:
    if not (url or "").startswith(("http://", "https://")):
        return ToolResult("http(s) URL required", is_error=True)
    try:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
            resp = await client.get(f"https://r.jina.ai/{url}", headers={"User-Agent": "AgentV/2.0"})
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"render failed: {exc}", is_error=True)
    if resp.status_code >= 400:
        return ToolResult(f"render failed: HTTP {resp.status_code}", is_error=True)
    limit = min(max(int(max_chars or 8000), 500), 30000)
    return ToolResult(resp.text[:limit])


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="text_to_speech",
            description="Convert text to a voice message and send it (edge-tts).",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}, "voice": {"type": "string"}},
                "required": ["text"],
            },
            handler=_tts,
            min_role="user",
        ),
        Tool(
            name="browser_read",
            description="Read a JavaScript-rendered web page as text via a rendering proxy.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer"}},
                "required": ["url"],
            },
            handler=_browser_read,
            min_role="user",
        ),
    ]
