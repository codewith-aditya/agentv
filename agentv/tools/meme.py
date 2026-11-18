"""Turn a workspace image into a captioned Telegram sticker (.webp)."""

from __future__ import annotations

import io
import textwrap
import uuid
from pathlib import Path

from .base import Tool, ToolContext, ToolResult

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


async def _make_sticker(ctx: ToolContext, path: str = "", text: str = "", uppercase: bool = True) -> ToolResult:
    if not path:
        return ToolResult("path to an image in the workspace is required", is_error=True)
    try:
        raw = await ctx.sandbox.read_file(ctx.ws_id, path)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"could not read {path}: {exc}", is_error=True)
    try:
        from PIL import Image, ImageDraw

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((512, 512))
        canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        canvas.paste(img, ((512 - img.width) // 2, (512 - img.height) // 2))
        draw = ImageDraw.Draw(canvas)
        caption = (text or "").strip()
        if uppercase:
            caption = caption.upper()
        if caption:
            font = _font(46)
            lines = textwrap.wrap(caption, width=14)[:3]
            line_h = 54
            total_h = line_h * len(lines)
            y = 512 - total_h - 24
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font, stroke_width=3)
                w = bbox[2] - bbox[0]
                x = (512 - w) // 2
                draw.text((x, y), line, font=font, fill="white", stroke_width=3, stroke_fill="black")
                y += line_h
        out = Path("/tmp") / f"sticker_{uuid.uuid4().hex[:8]}.webp"
        canvas.save(out, "WEBP", quality=92, method=4)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"sticker generation failed: {exc}", is_error=True)

    responder = ctx.responder
    if responder is not None and hasattr(responder, "send_sticker"):
        try:
            await responder.send_sticker(path=out)
            return ToolResult(f"Sticker created and sent: {out.name}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"sticker created but send failed: {exc}", is_error=True)
    return ToolResult(f"Sticker created: {out.name}", files=[out])


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="make_sticker",
            description="Turn an image in the workspace into a 512x512 Telegram sticker with a caption.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "workspace image path"},
                    "text": {"type": "string", "description": "caption text"},
                    "uppercase": {"type": "boolean"},
                },
                "required": ["path", "text"],
            },
            handler=_make_sticker,
            min_role="user",
        )
    ]
