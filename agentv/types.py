from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class Incoming:
    """Transport-agnostic inbound message."""

    transport: str  # "bot" | "user"
    chat_id: int
    chat_type: str  # private | group | supergroup | channel
    user_id: int
    text: str = ""
    username: str = ""
    first_name: str = ""
    chat_title: str = ""
    message_id: int = 0
    reply_to_id: int | None = None
    media: list[Path] = field(default_factory=list)
    media_kind: str = ""  # image | video | audio | document
    is_admin: bool = False
    role: str = ""  # resolved by core: owner | admin | user
    raw: Any = None

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"

    @property
    def is_group(self) -> bool:
        return self.chat_type in ("group", "supergroup")


@dataclass(slots=True)
class SentMessage:
    """Opaque handle the transport can edit/delete."""

    transport: str
    chat_id: int
    message_id: int
    raw: Any = None


@runtime_checkable
class Responder(Protocol):
    transport: str

    async def send(self, text: str, reply_to: int | None = None) -> SentMessage | None: ...

    async def edit(self, handle: SentMessage | None, text: str) -> None: ...

    async def delete(self, handle: SentMessage | None) -> None: ...

    async def send_file(self, path: Path | str, caption: str = "", reply_to: int | None = None) -> Any: ...

    async def typing(self, on: bool = True) -> None: ...

    async def is_admin(self, chat_id: int, user_id: int) -> bool: ...
