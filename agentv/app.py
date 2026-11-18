from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any

from .browser import close_browser
from .commands import CommandRouter
from .config import Settings, get_settings
from .core import AgentCore
from .crypto import SecretBox, load_or_create_secret
from .db import Database, ProviderRow
from .llm import LLMClient
from .log import setup_logging
from .mcp_hub import MCPHub
from .plugins_loader import PluginManager
from .sandbox import create_sandbox
from .scheduler import Notifier, ReminderScheduler
from .tools import build_registry
from .transports import BotApiTransport, UserbotTransport
from .web import WebServer

log = logging.getLogger(__name__)


class AgentVApp:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.db = Database(cfg.db_path)
        self.llm: LLMClient | None = None
        self.sandbox: Any = None
        self.mcp: MCPHub | None = None
        self.core: AgentCore | None = None
        self.web: WebServer | None = None
        self.transports: dict[str, Any] = {}
        self._tasks: list[asyncio.Task[Any]] = []
        self.notifier = Notifier()
        self.scheduler: ReminderScheduler | None = None

    # ------------------------------------------------------------------ startup
    async def start(self) -> None:
        cfg = self.cfg
        cfg.ensure_dirs()
        await self.db.connect()

        secret = cfg.secret_key or load_or_create_secret(cfg.data_path / "agentv.secret")
        box = SecretBox(secret)
        if cfg.bot_webhook_url and not cfg.bot_webhook_secret:
            import secrets as _secrets

            cfg.bot_webhook_secret = _secrets.token_urlsafe(24)
        await self._bootstrap_provider(box)

        self.llm = LLMClient(cfg)
        self.sandbox = await create_sandbox(cfg, log)
        registry = build_registry(self.db)
        self.mcp = MCPHub(cfg, self.db, self.sandbox)
        plugins = PluginManager(cfg, self.db, registry, CommandRouter())
        self.core = AgentCore(cfg, self.db, self.llm, self.sandbox, registry, self.mcp, plugins, box)
        plugins.router = self.core.router
        await self.core.start()

        if cfg.bot_api_enabled and cfg.bot_token:
            self.transports["bot"] = BotApiTransport(cfg, self.core)
        if cfg.userbot_enabled:
            self.transports["user"] = UserbotTransport(cfg, self.core)

        if not self.transports:
            raise RuntimeError(
                "Koi transport enabled nahi hai — BOT_TOKEN do ya USERBOT_ENABLED=true karo."
            )

        self.web = WebServer(cfg, self.core, self.transports)
        await self.web.start()

        for name, transport in self.transports.items():
            self.notifier.register(name, transport)
            self._tasks.append(asyncio.create_task(self._run_transport(name, transport), name=f"transport-{name}"))

        self.scheduler = ReminderScheduler(self.db, self.notifier, core=self.core)
        self.core.responder_factory = self.notifier.make_responder
        await self.scheduler.start()

        await asyncio.sleep(0.2)
        log.info("AgentV started (%s)", ", ".join(self.transports))
        if any(t.done() for t in self._tasks):
            for task in self._tasks:
                if task.done() and task.exception():
                    raise task.exception()  # type: ignore[misc]

    async def _run_transport(self, name: str, transport: Any) -> None:
        try:
            await transport.start()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("transport %s crashed", name)
            raise

    async def _bootstrap_provider(self, box: SecretBox) -> None:
        cfg = self.cfg
        existing = await self.db.get_provider("env")
        if existing is None and (cfg.llm_api_key or cfg.llm_base_url):
            await self.db.save_provider(
                name="env",
                base_url=cfg.llm_base_url.rstrip("/"),
                api_key_enc=box.encrypt(cfg.llm_api_key),
                model=cfg.llm_model,
                vision_model=cfg.llm_vision_model,
            )
            log.info("bootstrap provider 'env' → %s (%s)", cfg.llm_base_url, cfg.llm_model)
        if await self.db.default_provider() is None:
            default = "env" if await self.db.get_provider("env") else None
            if default:
                await self.db.set_setting("default_provider", default)

    # ------------------------------------------------------------------ shutdown
    async def stop(self) -> None:
        if self.scheduler is not None:
            await self.scheduler.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for transport in self.transports.values():
            try:
                await transport.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.web is not None:
            await self.web.stop()
        if self.core is not None:
            await self.core.stop()
        if self.sandbox is not None:
            await self.sandbox.close()
        await close_browser()
        if self.llm is not None:
            await self.llm.close()
        await self.db.close()
        log.info("AgentV stopped")


async def run(cfg: Settings | None = None) -> None:
    cfg = cfg or get_settings()
    setup_logging(cfg.log_level)
    app = AgentVApp(cfg)
    stop_event = asyncio.Event()

    def _signal(*_: Any) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal)
        except NotImplementedError:
            pass

    try:
        await app.start()
    except BaseException:
        # startup failed halfway: close DB/sandbox/MCP so threads don't keep the
        # process alive (aiosqlite's worker is non-daemon).
        try:
            await app.stop()
        except Exception:  # noqa: BLE001
            pass
        raise
    try:
        await stop_event.wait()
    finally:
        await app.stop()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        setup_logging("INFO")
        log.error("fatal: %s", exc)
        raise SystemExit(1) from exc
