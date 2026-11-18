from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Settings
from ..db import Database, ProviderRow
from ..llm import LLMClient
from ..sandbox import BaseSandbox

log = logging.getLogger(__name__)

ROLE_RANK = {"user": 1, "admin": 2, "owner": 3}


@dataclass(slots=True)
class ToolResult:
    content: str
    is_error: bool = False
    files: list[Path] = field(default_factory=list)
    display: str | None = None


@dataclass(slots=True)
class ToolContext:
    cfg: Settings
    db: Database
    sandbox: BaseSandbox
    chat_id: int
    chat_type: str
    user_id: int
    message_id: int = 0
    workspace_id: str = ""
    username: str = ""
    first_name: str = ""
    role: str = "user"
    transport: str = "bot"
    llm: LLMClient | None = None
    provider: ProviderRow | None = None
    tg: Any = None
    plugins: Any = None
    responder: Any = None

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def ws_id(self) -> str:
        return self.workspace_id or str(self.chat_id)

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"


Handler = Callable[..., Awaitable[ToolResult]]


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Handler,
        min_role: str = "owner",
        private_only: bool = False,
        group_only: bool = False,
        source: str = "builtin",
    ):
        self.name = name
        self.description = description
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.handler = handler
        self.min_role = min_role
        self.private_only = private_only
        self.group_only = group_only
        self.source = source

    def visible(self, ctx: ToolContext) -> bool:
        if ROLE_RANK.get(ctx.role, 0) < ROLE_RANK.get(self.min_role, 3):
            return False
        if self.private_only and not ctx.is_private:
            return False
        if self.group_only and ctx.is_private:
            return False
        return True

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, db: Database | None = None):
        self.db = db
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------ registry
    def register(self, tool: Tool) -> Tool:
        if not tool.name or len(tool.name) > 64:
            raise ValueError(f"invalid tool name: {tool.name!r}")
        self._tools[tool.name] = tool
        return tool

    def register_all(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def unregister_source(self, source: str) -> None:
        for name in [n for n, t in self._tools.items() if t.source == source]:
            del self._tools[name]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self, ctx: ToolContext) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values() if t.visible(ctx)]

    # ------------------------------------------------------------------ execution
    async def execute(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"Unknown tool: {name}", is_error=True)
        if not tool.visible(ctx):
            return ToolResult(f"Tool '{name}' is not permitted for role '{ctx.role}' here.", is_error=True)

        allowed = set((tool.parameters.get("properties") or {}).keys())
        kwargs = {k: v for k, v in (arguments or {}).items() if k in allowed}
        started = time.monotonic()
        try:
            result = await tool.handler(ctx, **kwargs)
        except TypeError as exc:
            result = ToolResult(f"Invalid arguments for {name}: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s failed", name)
            result = ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
        elapsed = int((time.monotonic() - started) * 1000)
        if self.db is not None:
            try:
                await self.db.audit(
                    chat_id=str(ctx.chat_id),
                    user_id=str(ctx.user_id),
                    tool=name,
                    args=json.dumps(arguments, ensure_ascii=False)[:2000],
                    ok=not result.is_error,
                    ms=elapsed,
                    error=result.content[:500] if result.is_error else "",
                )
            except Exception:  # noqa: BLE001
                log.debug("audit write failed", exc_info=True)
        return result
