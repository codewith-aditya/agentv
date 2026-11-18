from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .render import md_to_html
from .rich import to_rich_html
from .types import Incoming, Responder

ROLE_RANK = {"user": 1, "admin": 2, "owner": 3}


@dataclass
class CommandContext:
    inc: Incoming
    responder: Responder
    args: list[str]
    raw_args: str
    core: Any
    db: Any
    cfg: Any

    @property
    def chat_id(self) -> int:
        return self.inc.chat_id

    @property
    def role(self) -> str:
        return getattr(self.inc, "role", "user")

    async def reply(self, text: str) -> None:
        reply_to = self.inc.message_id or None
        if getattr(self.responder, "supports_rich", False):
            rich_html = to_rich_html(text)
            if rich_html:
                try:
                    if await self.responder.send_rich(rich_html, reply_to=reply_to):
                        return
                except Exception:  # noqa: BLE001
                    pass
        await self.responder.send(md_to_html(text), reply_to=reply_to)


CommandHandler = Callable[[CommandContext], Awaitable[None]]


@dataclass
class CommandSpec:
    name: str
    description: str
    handler: CommandHandler
    min_role: str = "user"
    usage: str = ""
    private_only: bool = False
    group_only: bool = False
    source: str = "builtin"


class CommandRouter:
    def __init__(self):
        self._commands: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> CommandSpec:
        self._commands[spec.name.lower().lstrip("/")] = spec
        return spec

    def add(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
        min_role: str = "user",
        usage: str = "",
        source: str = "builtin",
    ) -> CommandSpec:
        return self.register(
            CommandSpec(
                name=name,
                description=description,
                handler=handler,
                min_role=min_role,
                usage=usage,
                source=source,
            )
        )

    def get(self, name: str) -> CommandSpec | None:
        return self._commands.get(name.lower().lstrip("/"))

    def all(self) -> list[CommandSpec]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def unregister_source(self, source: str) -> None:
        for name in [n for n, c in self._commands.items() if c.source == source]:
            del self._commands[name]

    def allowed(self, spec: CommandSpec, role: str) -> bool:
        return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(spec.min_role, 1)
