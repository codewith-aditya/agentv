from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import web

from .config import Settings
from .core import AgentCore

log = logging.getLogger(__name__)


class WebServer:
    """Health, metrics and the optional Telegram webhook endpoint."""

    def __init__(self, cfg: Settings, core: AgentCore, transports: dict[str, Any]):
        self.cfg = cfg
        self.core = core
        self.transports = transports
        self.runner: web.AppRunner | None = None
        self.started_at = time.time()

    def _app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/healthz", self._health)
        app.router.add_get("/metrics", self._metrics)
        app.router.add_post("/telegram/webhook/{secret}", self._webhook)
        return app

    async def start(self) -> None:
        self.runner = web.AppRunner(self._app(), access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.cfg.web_host, self.cfg.web_port)
        await site.start()
        log.info("web on %s:%s", self.cfg.web_host, self.cfg.web_port)

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    # ------------------------------------------------------------------ handlers
    async def _health(self, request: web.Request) -> web.Response:
        sandbox = self.core.sandbox.health()
        bot = self.transports.get("bot")
        payload = {
            "ok": bool(sandbox.get("ok", True)),
            "uptime": int(time.time() - self.started_at),
            "sandbox": sandbox,
            "mcp": self.core.mcp.health(),
            "plugins": [p.name for p in self.core.plugins.plugins.values()],
            "transports": {
                "bot": bool(bot and getattr(bot, "bot", None)),
                "userbot": bool(self.transports.get("user") and getattr(self.transports.get("user"), "client", None)),
            },
        }
        return web.json_response(payload)

    async def _metrics(self, request: web.Request) -> web.Response:
        usage = await self.core.db.usage_totals()
        stats = await self.core.db.stats()
        lines = [
            "# HELP agentv_uptime_seconds Process uptime",
            "# TYPE agentv_uptime_seconds gauge",
            f"agentv_uptime_seconds {int(time.time() - self.started_at)}",
            "# TYPE agentv_tokens_total counter",
            f'agentv_tokens_total{{direction="in"}} {usage["prompt"]}',
            f'agentv_tokens_total{{direction="out"}} {usage["completion"]}',
            "# TYPE agentv_llm_calls_total counter",
            f"agentv_llm_calls_total {usage['calls']}",
            "# TYPE agentv_messages_total counter",
            f"agentv_messages_total {stats.get('messages', 0)}",
            "# TYPE agentv_tools_total gauge",
            f"agentv_tools_total {len(self.core.registry.all())}",
            "# TYPE agentv_mcp_tools_total gauge",
            f"agentv_mcp_tools_total {len(self.core.mcp.tool_specs())}",
        ]
        return web.Response(text="\n".join(lines) + "\n", content_type="text/plain")

    async def _webhook(self, request: web.Request) -> web.Response:
        secret = request.match_info.get("secret", "")
        expected = self.cfg.bot_webhook_secret
        if not expected or secret != expected:
            return web.Response(status=403, text="forbidden")
        transport = self.transports.get("bot")
        if transport is None:
            return web.Response(status=503, text="bot transport disabled")
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return web.Response(status=400, text="bad json")
        from aiogram.types import Update

        update = Update.model_validate(payload)
        await transport.feed_update(update)
        return web.Response(text="ok")
