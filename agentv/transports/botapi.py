from __future__ import annotations

import asyncio
import logging
import mimetypes
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import (
    BotCommand,
    ChatMemberUpdated,
    ChatPermissions,
    FSInputFile,
    InputRichMessage,
    Message,
    ReplyParameters,
)
from aiogram.types import User as TgUser

from ..config import Settings
from ..core import AgentCore, send_long
from ..types import Incoming, Responder, SentMessage

log = logging.getLogger(__name__)


def _chat_type(message: Message) -> str:
    ct = message.chat.type
    return str(getattr(ct, "value", ct))


def _media_ref(message: Message) -> tuple[Any, str, str] | None:
    if message.photo:
        return message.photo[-1], "image", "photo.jpg"
    if message.document:
        return message.document, "document", message.document.file_name or "document.bin"
    if message.video:
        return message.video, "video", message.video.file_name or "video.mp4"
    if message.audio:
        return message.audio, "audio", message.audio.file_name or "audio.mp3"
    if message.voice:
        return message.voice, "audio", "voice.ogg"
    if message.animation:
        return message.animation, "video", message.animation.file_name or "animation.mp4"
    if message.sticker:
        st = message.sticker
        if st.is_animated:
            return st, "sticker", "sticker.tgs"
        if st.is_video:
            return st, "sticker", "sticker.webm"
        return st, "sticker", "sticker.webp"
    if message.video_note:
        return message.video_note, "video", "video_note.mp4"
    return None


@dataclass
class Caps:
    """Feature flags learned at runtime (None = not tried yet)."""

    rich: bool | None = None
    drafts: bool | None = None


class BotResponder:
    transport = "bot"
    supports_expandable = True

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        telegram_ops: "BotTelegramOps | None" = None,
        cfg: Settings | None = None,
        caps: Caps | None = None,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.telegram_ops = telegram_ops
        self.cfg = cfg
        self.caps = caps or Caps()
        self._draft_id = random.randint(1, 2**31 - 1)
        self.tool_log_ttl = int(getattr(cfg, "tool_log_ttl", 0) or 0) if cfg else 0
        self.tool_log_details = bool(getattr(cfg, "tool_log_details", False)) if cfg else False
        self.thinking_limit = int(getattr(cfg, "thinking_limit_seconds", 20) or 20) if cfg else 20

    # ------------------------------------------------------------------ capabilities
    @property
    def supports_rich(self) -> bool:
        return (self.cfg is None or self.cfg.rich_messages != "off") and self.caps.rich is not False

    @property
    def supports_drafts(self) -> bool:
        return (self.cfg is None or self.cfg.draft_streaming != "off") and self.caps.drafts is not False

    def _is_private(self) -> bool:
        return self.chat_id > 0

    # ------------------------------------------------------------------ rich / drafts
    async def draft_update(self, html_text: str) -> bool:
        """Stream a native rich draft preview (private chats, Bot API 10.1+).

        The `<tg-thinking>` block only exists in drafts; that's what makes the
        Telegram "Thinking…" UI appear while the answer is being generated.
        """
        if not self.supports_drafts or not self._is_private():
            return False
        try:
            await self.bot.send_rich_message_draft(
                chat_id=self.chat_id,
                draft_id=self._draft_id,
                rich_message=InputRichMessage(html=(html_text or "")[:32000]),
                can_stop=True,
                keep_on_stop=True,
            )
            self.caps.drafts = True
            return True
        except TelegramBadRequest as exc:
            message = str(exc).lower()
            if any(word in message for word in ("not found", "unknown", "unsupported", "method")):
                self.caps.drafts = False
                log.info("drafts unsupported, falling back to edits: %s", exc)
            return False
        except Exception:  # noqa: BLE001
            log.debug("draft update failed", exc_info=True)
            return False

    async def send_rich(self, html_text: str, reply_to: int | None = None) -> SentMessage | None:
        """Send a persisted rich message; returns None so callers can fall back."""
        if not self.supports_rich:
            return None
        params = ReplyParameters(message_id=reply_to) if reply_to else None
        try:
            msg = await self.bot.send_rich_message(
                chat_id=self.chat_id,
                rich_message=InputRichMessage(html=html_text[:32000]),
                reply_parameters=params,
            )
            self.caps.rich = True
            return SentMessage("bot", self.chat_id, msg.message_id, msg)
        except TelegramBadRequest as exc:
            self.caps.rich = False
            log.info("rich messages unsupported, falling back to HTML: %s", exc)
            return None
        except Exception:  # noqa: BLE001
            log.debug("rich send failed", exc_info=True)
            return None

    async def edit_rich(self, handle: SentMessage | None, html_text: str) -> bool:
        """Edit an existing message into a rich message (single-message finalize)."""
        if handle is None or not self.supports_rich:
            return False
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=handle.message_id,
                rich_message=InputRichMessage(html=html_text[:32000]),
            )
            self.caps.rich = True
            return True
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                return True
            self.caps.rich = False
            log.info("rich edit unsupported, falling back: %s", exc)
            return False
        except Exception:  # noqa: BLE001
            log.debug("rich edit failed", exc_info=True)
            return False

    async def _retry(self, factory, attempts: int = 3):
        """Run a Telegram call, sleeping through FloodWait (429) responses."""
        for attempt in range(attempts):
            try:
                return await factory()
            except TelegramRetryAfter as exc:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(float(exc.retry_after) + 0.5)

    # ------------------------------------------------------------------ basic send
    async def send(self, text: str, reply_to: int | None = None) -> SentMessage | None:
        params = ReplyParameters(message_id=reply_to) if reply_to else None
        try:
            msg = await self._retry(
                lambda: self.bot.send_message(
                    self.chat_id, text, reply_parameters=params, disable_web_page_preview=True
                )
            )
            return SentMessage("bot", self.chat_id, msg.message_id, msg)
        except TelegramBadRequest as exc:
            if "parse" in str(exc).lower() or "entity" in str(exc).lower():
                plain = text.replace("<", "&lt;").replace(">", "&gt;")
                msg = await self._retry(
                    lambda: self.bot.send_message(self.chat_id, plain, reply_parameters=params)
                )
                return SentMessage("bot", self.chat_id, msg.message_id, msg)
            raise

    async def edit(self, handle: SentMessage | None, text: str) -> None:
        if handle is None:
            return
        try:
            await self._retry(
                lambda: self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=handle.message_id,
                    text=text,
                    disable_web_page_preview=True,
                )
            )
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc).lower():
                raise

    async def delete(self, handle: SentMessage | None) -> None:
        if handle is None:
            return
        try:
            await self._retry(lambda: self.bot.delete_message(self.chat_id, handle.message_id))
        except TelegramBadRequest:
            pass

    async def send_file(self, path: Path | str, caption: str = "", reply_to: int | None = None) -> Any:
        local = Path(path)
        limit = (self.cfg.max_download_mb * 1024 * 1024) if self.cfg else 0
        if limit and local.exists() and local.stat().st_size > limit:
            await self.send(
                f"⚠️ {local.name} is {local.stat().st_size / 1024 / 1024:.1f} MB — "
                f"download limit is {self.cfg.max_download_mb} MB.",
                reply_to=reply_to,
            )
            return
        params = ReplyParameters(message_id=reply_to) if reply_to else None
        file = FSInputFile(str(local), filename=local.name)
        try:
            await self.bot.send_document(self.chat_id, file, caption=caption[:1024], reply_parameters=params)
        except TelegramForbiddenError:
            await self.bot.send_message(self.chat_id, f"⚠️ could not send {Path(path).name}")

    async def send_sticker(self, path: Path | str | None = None, file_id: str = "") -> Any:
        if file_id:
            return await self.bot.send_sticker(self.chat_id, file_id)
        if not path:
            raise ValueError("sticker path or file_id required")
        return await self.bot.send_sticker(self.chat_id, FSInputFile(str(path)))

    async def send_animation(self, path: Path | str, caption: str = "") -> Any:
        return await self.bot.send_animation(self.chat_id, FSInputFile(str(path)), caption=caption[:1024])

    async def send_animation_id(self, file_id: str, caption: str = "") -> Any:
        return await self.bot.send_animation(self.chat_id, file_id, caption=caption[:1024])

    async def send_voice(self, path: Path | str, caption: str = "") -> Any:
        return await self.bot.send_voice(self.chat_id, FSInputFile(str(path)), caption=caption[:1024])

    async def send_photo(self, path: Path | str, caption: str = "") -> Any:
        return await self.bot.send_photo(self.chat_id, FSInputFile(str(path)), caption=caption[:1024])

    async def typing(self, on: bool = True) -> None:
        if not on:
            return
        try:
            await self.bot.send_chat_action(self.chat_id, ChatAction.TYPING)
        except Exception:  # noqa: BLE001
            pass

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
            return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
        except Exception:  # noqa: BLE001
            return False


class BotTelegramOps:
    """Telegram operations backed by the Bot API (subset of userbot abilities)."""

    transport = "bot"

    def __init__(self, bot: Bot):
        self.bot = bot

    async def lookup(self, target: str) -> str:
        try:
            chat = await self.bot.get_chat(target)
        except Exception as exc:  # noqa: BLE001
            return f"❌ lookup failed: {exc}"
        lines = [f"id: {chat.id}", f"type: {getattr(chat.type, 'value', chat.type)}"]
        if chat.title:
            lines.append(f"title: {chat.title}")
        if chat.username:
            lines.append(f"username: @{chat.username}")
        try:
            lines.append(f"members: {await self.bot.get_chat_member_count(chat.id)}")
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(lines)

    async def chat_stats(self, chat_id: int) -> str:
        try:
            chat = await self.bot.get_chat(chat_id)
            count = await self.bot.get_chat_member_count(chat_id)
        except Exception as exc:  # noqa: BLE001
            return f"❌ stats failed: {exc}"
        return (
            f"📊 {chat.title or chat.id}\n"
            f"id: {chat.id}\n"
            f"members: {count}\n"
            f"username: @{chat.username}" if chat.username else f"📊 {chat.title or chat.id}\nid: {chat.id}\nmembers: {count}"
        )

    async def manage(self, chat_id: int, user: str, action: str, minutes: int | None = None) -> str:
        try:
            user_id = int(str(user).lstrip("@"))
        except ValueError:
            return "Numeric user id do (Bot API @username resolve nahi kar sakta)."
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes) if minutes else None
        try:
            if action == "kick":
                await self.bot.ban_chat_member(chat_id, user_id)
                await self.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
                return f"👢 kicked {user_id}"
            if action == "ban":
                await self.bot.ban_chat_member(chat_id, user_id, until_date=until)
                return f"🔨 banned {user_id}" + (f" for {minutes}m" if minutes else " permanently")
            if action == "unban":
                await self.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
                return f"🕊️ unbanned {user_id}"
            if action == "mute":
                await self.bot.restrict_chat_member(
                    chat_id, user_id, permissions=ChatPermissions(can_send_messages=False), until_date=until
                )
                return f"🔇 muted {user_id}" + (f" for {minutes}m" if minutes else " permanently")
            if action == "unmute":
                await self.bot.restrict_chat_member(
                    chat_id,
                    user_id,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_audios=True,
                        can_send_documents=True,
                        can_send_photos=True,
                        can_send_videos=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                    ),
                )
                return f"🔊 unmuted {user_id}"
        except Exception as exc:  # noqa: BLE001
            return f"❌ {action} failed: {exc}"
        return "unknown action"

    async def delete_message(self, chat_id: int, message_id: int) -> str:
        try:
            await self.bot.delete_message(chat_id, message_id)
            return f"🗑 deleted message {message_id}"
        except Exception as exc:  # noqa: BLE001
            return f"❌ delete failed: {exc}"

    async def send_dm(self, user_id: int, text: str) -> None:
        await self.bot.send_message(user_id, text, parse_mode="HTML")

    async def pin(self, chat_id: int, message_id: int, notify: bool = False) -> str:
        try:
            await self.bot.pin_chat_message(chat_id, message_id, disable_notification=not notify)
            return f"📌 pinned message {message_id}"
        except Exception as exc:  # noqa: BLE001
            return f"❌ pin failed: {exc}"

    async def unpin(self, chat_id: int, message_id: int = 0) -> str:
        try:
            if message_id:
                await self.bot.unpin_chat_message(chat_id, message_id)
            else:
                await self.bot.unpin_chat_message(chat_id)
            return "📌 unpinned"
        except Exception as exc:  # noqa: BLE001
            return f"❌ unpin failed: {exc}"

    async def react(self, chat_id: int, message_id: int, emoji: str) -> str:
        from aiogram.types import ReactionTypeEmoji

        try:
            await self.bot.set_message_reaction(
                chat_id, message_id, reaction=[ReactionTypeEmoji(emoji=emoji)]
            )
            return f"Reacted {emoji} to message {message_id}."
        except Exception as exc:  # noqa: BLE001
            return f"❌ reaction failed: {exc}"

    async def leave(self, chat_id: int) -> str:
        try:
            await self.bot.leave_chat(chat_id)
            return "👋 left the chat"
        except Exception as exc:  # noqa: BLE001
            return f"❌ leave failed: {exc}"


class BotApiTransport:
    name = "bot"

    def __init__(self, cfg: Settings, core: AgentCore):
        self.cfg = cfg
        self.core = core
        self.bot: Bot | None = None
        self.dp: Dispatcher | None = None
        self.username = ""
        self.ops: BotTelegramOps | None = None
        self.caps = Caps()
        self._polling_task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------ setup
    def _build_router(self) -> Router:
        router = Router(name="agentv")

        @router.my_chat_member()
        async def on_member(update: ChatMemberUpdated) -> None:
            status = str(getattr(update.new_chat_member.status, "value", update.new_chat_member.status))
            if status not in ("member", "administrator") or _chat_type_any(update):
                return
            if update.from_user and update.from_user.id in self.cfg.owners:
                await self.core.db.authorize_group(
                    update.chat.id, getattr(update.chat, "title", "") or "", update.from_user.id
                )
                log.info("authorized group %s (%s)", update.chat.id, getattr(update.chat, "title", ""))
            else:
                try:
                    await self.bot.leave_chat(update.chat.id)  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    pass

        @router.stopped_message_generation()
        async def on_generation_stopped(event: Any) -> None:
            chat = getattr(event, "chat", None)
            chat_id = getattr(chat, "id", None)
            if chat_id is not None:
                await self.core.cancel_turn(chat_id)

        @router.message()
        async def on_message(message: Message) -> None:
            await self._handle_message(message)

        return router

    async def start(self) -> None:
        if not self.cfg.bot_token:
            raise RuntimeError("BOT_TOKEN missing")
        self.bot = Bot(
            token=self.cfg.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        me = await self.bot.get_me()
        self.username = me.username or ""
        self.core.bot_username = self.username
        self.ops = BotTelegramOps(self.bot)
        self.dp = Dispatcher()
        self.dp.include_router(self._build_router())
        await self._set_commands()
        if self.cfg.bot_webhook_url:
            url = self.cfg.bot_webhook_url.rstrip("/") + f"/telegram/webhook/{self.cfg.bot_webhook_secret}"
            await self.bot.set_webhook(
                url, secret_token=self.cfg.bot_webhook_secret, drop_pending_updates=False
            )
            log.info("bot @%s webhook mode → %s", self.username, url)
            return
        log.info("bot @%s polling", self.username)
        await self.dp.start_polling(self.bot, handle_signals=False)

    async def _set_commands(self) -> None:
        assert self.bot is not None
        cmds = [
            BotCommand(command="start", description="Start"),
            BotCommand(command="help", description="All commands"),
            BotCommand(command="new", description="Clear chat history"),
            BotCommand(command="stop", description="Cancel running turn"),
            BotCommand(command="status", description="System status"),
            BotCommand(command="model", description="Show/switch model"),
            BotCommand(command="models", description="List provider models"),
        ]
        try:
            await self.bot.set_my_commands(cmds)
        except Exception:  # noqa: BLE001
            pass

    async def stop(self) -> None:
        if self.dp is not None:
            try:
                await self.dp.stop_polling()
            except Exception:  # noqa: BLE001
                pass
        if self.bot is not None:
            await self.bot.session.close()

    # ------------------------------------------------------------------ inbound
    async def _handle_message(self, message: Message) -> None:
        assert self.bot is not None
        if message.from_user is None or message.from_user.is_bot:
            return
        chat_type = _chat_type(message)
        if chat_type not in ("private", "group", "supergroup"):
            return

        is_group = chat_type in ("group", "supergroup")
        if is_group:
            authorized = await self.core.db.is_group_authorized(message.chat.id)
            if not authorized:
                if message.from_user.id in self.cfg.owners:
                    await self.core.db.authorize_group(
                        message.chat.id, message.chat.title or "", message.from_user.id
                    )
                    authorized = True
                else:
                    return
            if not self._addressed(message) and not (message.text or "").startswith("/"):
                log.info(
                    "group message ignored chat=%s user=%s text=%r",
                    message.chat.id,
                    message.from_user.id,
                    (message.text or message.caption or "")[:120],
                )
                # still record it for group history/search/summaries
                if message.text or message.caption:
                    await self.core.db.add_group_message(
                        message.chat.id,
                        message.message_id,
                        message.from_user.id,
                        message.from_user.first_name or message.from_user.username or "",
                        message.text or message.caption or "",
                    )
                return

        inc = await self._to_incoming(message, chat_type)
        if inc is None:
            return
        if chat_type in ("group", "supergroup") and (inc.text or inc.media):
            await self.core.db.add_group_message(
                message.chat.id,
                message.message_id,
                message.from_user.id,
                message.from_user.first_name or message.from_user.username or "",
                inc.text or "[media]",
            )
        if message.sticker:
            key = message.sticker.emoji or f"sticker_{message.sticker.file_unique_id[:10]}"
            try:
                await self.core.db.save_sticker_file(key, message.sticker.file_id, "sticker")
            except Exception:  # noqa: BLE001
                pass
        if message.animation:
            key = f"gif_{message.animation.file_unique_id[:10]}"
            try:
                await self.core.db.save_sticker_file(key, message.animation.file_id, "gif")
            except Exception:  # noqa: BLE001
                pass
        responder = BotResponder(self.bot, message.chat.id, self.ops, self.cfg, self.caps)
        if (inc.text or "").startswith("/"):
            if await self.core.handle_command(inc, responder):
                return
        await self.core.handle_message(inc, responder)

    def _addressed(self, message: Message) -> bool:
        assert self.bot is not None
        if message.reply_to_message and message.reply_to_message.from_user:
            if message.reply_to_message.from_user.id == self.bot.id:
                return True
        text = message.text or message.caption or ""
        if self.username and f"@{self.username}".lower() in text.lower():
            return True
        for entity in (message.entities or []) + (message.caption_entities or []):
            etype = str(getattr(entity.type, "value", entity.type))
            if etype in ("mention", "text_mention"):
                return True
        return False

    async def _to_incoming(self, message: Message, chat_type: str) -> Incoming | None:
        assert self.bot is not None
        assert message.from_user is not None
        text = message.text or message.caption or ""
        media: list[Path] = []
        media_kind = ""
        if message.sticker and message.sticker.emoji:
            text = (text + " " + message.sticker.emoji).strip()

        ref = _media_ref(message)
        if ref is not None:
            obj, media_kind, default_name = ref
            size = int(getattr(obj, "file_size", 0) or 0)
            limit = self.cfg.max_upload_mb * 1024 * 1024
            if limit and size > limit:
                try:
                    await message.reply(
                        f"⚠️ File is {size / 1024 / 1024:.1f} MB — upload limit is "
                        f"{self.cfg.max_upload_mb} MB."
                    )
                except Exception:  # noqa: BLE001
                    pass
                return None
            filename = Path(default_name).name
            if "." not in filename:
                mime = getattr(obj, "mime_type", "") or ""
                ext = mimetypes.guess_extension(mime) or ""
                filename += ext
            folder = self.cfg.uploads_path / f"{message.chat.id}_{message.message_id}"
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / filename
            try:
                await self.bot.download(obj, destination=dest)
                media.append(dest)
            except Exception:  # noqa: BLE001
                log.exception("media download failed")

        is_admin = False
        if chat_type in ("group", "supergroup"):
            try:
                member = await self.bot.get_chat_member(message.chat.id, message.from_user.id)
                is_admin = str(getattr(member.status, "value", member.status)) in ("administrator", "creator")
            except Exception:  # noqa: BLE001
                pass

        return Incoming(
            transport="bot",
            chat_id=message.chat.id,
            chat_type=chat_type,
            user_id=message.from_user.id,
            text=text,
            username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
            chat_title=message.chat.title or "",
            message_id=message.message_id,
            reply_to_id=message.reply_to_message.message_id if message.reply_to_message else None,
            media=media,
            media_kind=media_kind,
            is_admin=is_admin,
            raw=message,
        )

    # ------------------------------------------------------------------ outbound (reminders)
    async def send_text(self, chat_id: int, text: str) -> None:
        if self.bot is None:
            raise RuntimeError("bot transport not started")
        responder = BotResponder(self.bot, chat_id, self.ops, self.cfg, self.caps)
        await send_long(responder, chat_id, text)

    def make_responder(self, chat_id: int) -> BotResponder:
        if self.bot is None:
            raise RuntimeError("bot transport not started")
        return BotResponder(self.bot, chat_id, self.ops, self.cfg, self.caps)

    # ------------------------------------------------------------------ webhook
    async def feed_update(self, update: Any) -> None:
        if self.dp is not None and self.bot is not None:
            await self.dp.feed_update(self.bot, update)


def _chat_type_any(update: ChatMemberUpdated) -> bool:
    return str(getattr(update.chat.type, "value", update.chat.type)) not in ("group", "supergroup")
