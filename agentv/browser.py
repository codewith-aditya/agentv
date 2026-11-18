"""Shared headless Chromium for the browser tool (Playwright)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

_manager: "BrowserManager | None" = None


class BrowserManager:
    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()
        self._tmp = Path("/tmp/agentv-browser")
        self._tmp.mkdir(parents=True, exist_ok=True)

    async def _ensure(self):
        if self._page is not None:
            return self._page
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 AgentV/2.0",
        )
        self._page = await context.new_page()
        return self._page

    async def _goto(self, url: str, wait_ms: int = 1200):
        page = await self._ensure()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if wait_ms:
            await page.wait_for_timeout(wait_ms)
        return page

    async def read(self, url: str, selector: str = "") -> str:
        async with self._lock:
            page = await self._goto(url)
            try:
                if selector:
                    el = await page.query_selector(selector)
                    text = await el.inner_text() if el else ""
                else:
                    text = await page.inner_text("body")
            except Exception:
                text = await page.inner_text("body")
        return (text or "").strip()

    async def screenshot(self, url: str, full_page: bool = False) -> Path:
        async with self._lock:
            page = await self._goto(url)
            path = self._tmp / f"shot_{uuid.uuid4().hex[:8]}.png"
            await page.screenshot(path=str(path), full_page=full_page)
        return path

    async def click(self, url: str, selector: str) -> str:
        async with self._lock:
            page = await self._goto(url)
            await page.click(selector, timeout=10000)
            await page.wait_for_timeout(800)
            return (await page.inner_text("body") or "").strip()

    async def fill(self, url: str, selector: str, text: str, submit: bool = False) -> str:
        async with self._lock:
            page = await self._goto(url)
            await page.fill(selector, text, timeout=10000)
            if submit:
                await page.press(selector, "Enter")
                await page.wait_for_timeout(1200)
            return (await page.inner_text("body") or "").strip()

    async def close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._browser = self._pw = self._page = None


def get_browser() -> BrowserManager:
    global _manager
    if _manager is None:
        _manager = BrowserManager()
    return _manager


async def close_browser() -> None:
    global _manager
    if _manager is not None:
        await _manager.close()
        _manager = None
