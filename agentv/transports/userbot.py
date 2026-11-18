from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, UserAdminInvalidError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import EditBannedRequest, GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import Channel, Chat, ChatBannedRights, User

from ..config import Settings
from ..core import AgentCore, send_long
from ..types import Incoming, Responder, SentMessage

log = logging.getLogger(__name__)


class UserbotResponder:
    transport = "user"
    supports_rich = False
    supports_drafts = False
    supports_expandable = False

    def __init__(self, client: TelegramClient, chat_id: int, ops: "UserbotOps"):
        self.client = client
        self.chat_id = chat_id
        self.telegram_ops = ops

    async def send(self, text: str, reply_to: int | None = None) -> SentMessage | None:
        msg = await self.client.send_message(self.chat_id, text, parse_mode="html", reply_to=reply_to)
        return SentMessage("user", self.chat_id, msg.id, msg)

    async def edit(self, handle: SentMessage | None, text: str) -> None:
        if handle is None:
            return
        try:
            await self.client.edit_message(self.chat_id, handle.message_id, text, parse_mode="html")
        except Exception as exc:  # noqa: BLE001
            if "not modified" not in str(exc).lower():
                raise

    async def delete(self, handle: SentMessage | None) -> None:
        if handle is None:
            return
        try:
            await self.client.delete_messages(self.chat_id, [handle.message_id])
        except Exception:  # noqa: BLE001
            pass

    async def send_file(self, path: Path | str, caption: str = "", reply_to: int | None = None) -> Any:
        await self.client.send_file(self.chat_id, str(path), caption=caption[:1024], reply_to=reply_to)

    async def send_voice(self, path: Path | str, caption: str = "") -> Any:
        await self.client.send_file(self.chat_id, str(path), caption=caption[:1024], voice_note=True)

    async def send_photo(self, path: Path | str, caption: str = "") -> Any:
        await self.client.send_file(self.chat_id, str(path), caption=caption[:1024])

    async def typing(self, on: bool = True) -> None:
        if not on:
            return
        asyncio.create_task(self._action())

    async def _action(self) -> None:
        try:
            async with self.client.action(self.chat_id, "typing"):
                await asyncio.sleep(1.0)
        except Exception:  # noqa: BLE001
            pass

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        try:
            perms = await self.client.get_permissions(chat_id, user_id)
            return bool(getattr(perms, "is_admin", False))
        except Exception:  # noqa: BLE001
            return False


class UserbotOps:
    transport = "user"

    def __init__(self, client: TelegramClient):
        self.client = client

    async def lookup(self, target: str) -> str:
        try:
            value: Any = int(target) if str(target).lstrip("-").isdigit() else target
            entity = await self.client.get_entity(value)
        except Exception as exc:  # noqa: BLE001
            return f"❌ lookup failed: {exc}"
        lines = [f"id: {getattr(entity, 'id', '?')}", f"type: {type(entity).__name__}"]
        for attr in ("first_name", "last_name", "title", "username", "phone"):
            val = getattr(entity, attr, None)
            if val:
                lines.append(f"{attr}: {val}")
        if getattr(entity, "bot", False):
            lines.append("bot: yes")
        try:
            if isinstance(entity, User):
                if entity.status:
                    lines.append(f"status: {type(entity.status).__name__}")
            elif isinstance(entity, Channel):
                full = await self.client(GetFullChannelRequest(entity))
                lines.append(f"members: {getattr(full.full_chat, 'participants_count', '?')}")
                if getattr(full.full_chat, "about", None):
                    lines.append(f"about: {full.full_chat.about}")
            elif isinstance(entity, Chat):
                full = await self.client(GetFullChatRequest(entity.id))
                lines.append(f"members: {getattr(full.full_chat, 'participants_count', '?')}")
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(lines)

    async def chat_stats(self, chat_id: int) -> str:
        try:
            entity = await self.client.get_entity(chat_id)
        except Exception as exc:  # noqa: BLE001
            return f"❌ stats failed: {exc}"
        total, online = 0, 0
        try:
            if isinstance(entity, Channel):
                full = await self.client(GetFullChannelRequest(entity))
                total = getattr(full.full_chat, "participants_count", 0) or 0
                online = getattr(full.full_chat, "online_count", 0) or 0
            else:
                full = await self.client(GetFullChatRequest(entity.id))
                total = getattr(full.full_chat, "participants_count", 0) or 0
        except Exception:  # noqa: BLE001
            pass
        title = getattr(entity, "title", "chat")
        return f"📊 {title}\nid: {getattr(entity, 'id', '?')}\nmembers: {total}\nonline: {online}"

    async def manage(self, chat_id: int, user: str, action: str, minutes: int | None = None) -> str:
        try:
            entity = await self.client.get_entity(chat_id)
            target = await self.client.get_entity(
                int(user) if str(user).lstrip("-").isdigit() else user
            )
        except Exception as exc:  # noqa: BLE001
            return f"❌ user resolve failed: {exc}"
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes) if minutes else None
        try:
            if action == "kick":
                await self.client.kick_participant(entity, target)
                return f"👢 kicked {getattr(target, 'first_name', user)}"
            if action == "ban":
                await self.client.edit_permissions(entity, target, view_messages=False, until_date=until)
                return f"🔨 banned {getattr(target, 'first_name', user)}"
            if action == "unban":
                await self.client.edit_permissions(entity, target, view_messages=True)
                return f"🕊️ unbanned {getattr(target, 'first_name', user)}"
            if action == "mute":
                await self.client.edit_permissions(entity, target, send_messages=False, until_date=until)
                return f"🔇 muted {getattr(target, 'first_name', user)}"
            if action == "unmute":
                await self.client.edit_permissions(entity, target, send_messages=True)
                return f"🔊 unmuted {getattr(target, 'first_name', user)}"
        except FloodWaitError as exc:
            return f"⏳ flood wait {exc.seconds}s"
        except UserAdminInvalidError:
            return "❌ admin rights nahi hain."
        except Exception as exc:  # noqa: BLE001
            return f"❌ {action} failed: {exc}"
        return "unknown action"

    async def delete_message(self, chat_id: int, message_id: int) -> str:
        try:
            await self.client.delete_messages(chat_id, [message_id])
            return f"🗑 deleted {message_id}"
        except Exception as exc:  # noqa: BLE001
            return f"❌ delete failed: {exc}"

    async def send_dm(self, user_id: int, text: str) -> None:
        await self.client.send_message(user_id, text, parse_mode="html")

    async def pin(self, chat_id: int, message_id: int, notify: bool = False) -> str:
        try:
            await self.client.pin_message(chat_id, message_id, notify=notify)
            return f"📌 pinned message {message_id}"
        except Exception as exc:  # noqa: BLE001
            return f"❌ pin failed: {exc}"

    async def unpin(self, chat_id: int, message_id: int = 0) -> str:
        try:
            await self.client.unpin_message(chat_id, message_id or None)
            return "📌 unpinned"
        except Exception as exc:  # noqa: BLE001
            return f"❌ unpin failed: {exc}"

    async def react(self, chat_id: int, message_id: int, emoji: str) -> str:
        from telethon.tl.functions.messages import SendReactionRequest
        from telethon.tl.types import ReactionEmoji

        try:
            await self.client(
                SendReactionRequest(peer=chat_id, msg_id=message_id, reaction=[ReactionEmoji(emoticon=emoji)])
            )
            return f"Reacted {emoji} to message {message_id}."
        except Exception as exc:  # noqa: BLE001
            return f"❌ reaction failed: {exc}"

    async def leave(self, chat_id: int) -> str:
        try:
            await self.client.delete_dialog(chat_id)
            return "👋 left"
        except Exception as exc:  # noqa: BLE001
            return f"❌ leave failed: {exc}"


class UserbotTransport:
    """MTProto userbot: acts as the owner's account, triggered by a prefix."""

    name = "user"

    def __init__(self, cfg: Settings, core: AgentCore):
        self.cfg = cfg
        self.core = core
        self.client: TelegramClient | None = None
        self.me: Any = None
        self.ops: UserbotOps | None = None
        self._own: set[int] = set()

    async def start(self) -> None:
        if not (self.cfg.tg_api_id and self.cfg.tg_api_hash):
            raise RuntimeError("TG_API_ID / TG_API_HASH missing")
        session: Any
        raw = self.cfg.userbot_session.strip()
        if raw.startswith("1"):
            session = StringSession(raw)
        elif raw:
            session = str(Path(raw).expanduser())
        else:
            session = str(self.cfg.data_path / "userbot")
        self.client = TelegramClient(session, self.cfg.tg_api_id, self.cfg.tg_api_hash, connection_retries=5)
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("userbot session authorized nahi hai (SESSION_STRING regenerate karo)")
        self.me = await self.client.get_me()
        self.ops = UserbotOps(self.client)
        self.client.add_event_handler(self._on_message, events.NewMessage())
        log.info("userbot @%s online", getattr(self.me, "username", self.me.id))
        await self.client.run_until_disconnected()

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.disconnect()

    async def send_text(self, chat_id: int, text: str) -> None:
        if self.client is None or self.ops is None:
            raise RuntimeError("userbot transport not started")
        responder = UserbotResponder(self.client, chat_id, self.ops)
        await send_long(responder, chat_id, text)

    def make_responder(self, chat_id: int) -> UserbotResponder:
        if self.client is None or self.ops is None:
            raise RuntimeError("userbot transport not started")
        return UserbotResponder(self.client, chat_id, self.ops)

    # ------------------------------------------------------------------ handler
    async def _on_message(self, event: events.NewMessage.Event) -> None:
        if self.client is None or self.ops is None:
            return
        text = (event.raw_text or "").strip()
        if not text:
            return
        if event.id in self._own:
            return
        if getattr(event, "sender_id", None) == getattr(self.me, "id", None) and not event.out:
            return

        prefix = self.cfg.userbot_prefix or "."
        if event.out:
            if not text.startswith(prefix):
                return
        else:
            if not event.is_private:
                return
            allowed = self.cfg.allowed_userbot_users | self.cfg.owners
            if event.sender_id not in allowed:
                if not (event.is_reply and await self._reply_is_own(event)):
                    return
            if text.startswith(prefix):
                pass
            elif event.is_reply and await self._reply_is_own(event):
                pass
            else:
                return

        if not (text.startswith(prefix) or text.startswith("/")):
            return
        command_text = "/" + text[len(prefix):] if text.startswith(prefix) else text
        inc = await self._to_incoming(event, command_text)
        if inc is None:
            return
        responder = UserbotResponder(self.client, event.chat_id, self.ops)
        if (inc.text or "").startswith("/"):
            if await self.core.handle_command(inc, responder):
                return
        await self.core.handle_message(inc, responder)

    async def _reply_is_own(self, event: events.NewMessage.Event) -> bool:
        try:
            reply = await event.get_reply_message()
            return bool(reply and reply.out)
        except Exception:  # noqa: BLE001
            return False

    async def _to_incoming(self, event: events.NewMessage.Event, text: str) -> Incoming | None:
        assert self.client is not None
        chat_type = "private"
        if event.is_group:
            chat_type = "supergroup" if getattr(event.chat, "megagroup", False) else "group"
        elif event.is_channel:
            chat_type = "channel"

        media: list[Path] = []
        if event.media:
            folder = self.cfg.uploads_path / f"{event.chat_id}_{event.id}"
            folder.mkdir(parents=True, exist_ok=True)
            try:
                path = await event.download_media(file=str(folder))
                if path:
                    p = Path(path)
                    media.append(p if p.is_file() else Path(str(path)))
            except Exception:  # noqa: BLE001
                log.exception("userbot media download failed")

        sender = await event.get_sender()
        username = getattr(sender, "username", "") or ""
        first_name = getattr(sender, "first_name", "") or getattr(sender, "title", "") or ""
        is_admin = False
        if event.is_group:
            try:
                perms = await self.client.get_permissions(event.chat_id, event.sender_id)
                is_admin = bool(getattr(perms, "is_admin", False))
            except Exception:  # noqa: BLE001
                pass

        return Incoming(
            transport="user",
            chat_id=event.chat_id,
            chat_type=chat_type,
            user_id=event.sender_id or 0,
            text=text,
            username=username,
            first_name=first_name,
            chat_title=getattr(event.chat, "title", "") or "",
            message_id=event.id,
            reply_to_id=event.reply_to_msg_id,
            media=media,
            media_kind="image" if media and any(p.suffix.lower() in (".jpg", ".png", ".webp") for p in media) else ("document" if media else ""),
            is_admin=is_admin,
            raw=event,
        )
