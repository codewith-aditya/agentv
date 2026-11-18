"""Delivery of scheduled messages (reminders) across transports."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .db import Database

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self) -> None:
        self._transports: dict[str, Any] = {}

    def register(self, name: str, transport: Any) -> None:
        self._transports[name] = transport

    def unregister(self, name: str) -> None:
        self._transports.pop(name, None)

    def make_responder(self, chat_id: int, transport: str):
        t = self._transports.get(transport) or self._transports.get("bot") or self._transports.get("user")
        if t is None or not hasattr(t, "make_responder"):
            return None
        return t.make_responder(chat_id)

    async def send(self, chat_id: int, text: str, preferred: str | None = None) -> bool:
        order = [preferred] if preferred else []
        order += [name for name in ("bot", "user") if name not in order]
        for name in order:
            transport = self._transports.get(name)
            if transport is None or not hasattr(transport, "send_text"):
                continue
            try:
                await transport.send_text(chat_id, text)
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("reminder delivery via %s failed: %s", name, exc)
        return False


class ReminderScheduler:
    """Polls due reminders and delivers them; retries at most 3 times."""

    def __init__(self, db: Database, notifier: Notifier, interval: int = 20, core=None):
        self.db = db
        self.notifier = notifier
        self.core = core
        self.interval = max(5, interval)
        self._task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="reminder-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                try:
                    await self.tick()
                except Exception:  # noqa: BLE001
                    log.exception("reminder tick failed")
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            return

    async def tick(self) -> int:
        rows = await self.db.due_reminders(int(time.time()))
        delivered = 0
        for row in rows:
            text = f"⏰ **Reminder**\n{row['text']}"
            ok = await self.notifier.send(int(row["chat_id"]), text, row["transport"])
            if ok:
                await self.db.mark_reminder_sent(int(row["id"]))
                delivered += 1
            else:
                await self.db.mark_reminder_failed(int(row["id"]), "no transport available")

        # recurring schedules: run a full agent turn when possible
        now = int(time.time())
        for row in await self.db.due_schedules(now):
            if self.core is not None and getattr(self.core, "responder_factory", None) is not None:
                asyncio.create_task(
                    self.core.run_scheduled(
                        int(row["chat_id"]), row["transport"] or "bot", row["user_id"] or "", row["prompt"]
                    )
                )
            else:
                await self.notifier.send(
                    int(row["chat_id"]), f"⏰ **Scheduled task**\n{row['prompt']}", row["transport"]
                )
            delivered += 1
            await self.db.advance_schedule(int(row["id"]), now + int(row["interval_seconds"]))
        return delivered
