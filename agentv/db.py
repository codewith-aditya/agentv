from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    role        TEXT NOT NULL DEFAULT 'user',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT
);

CREATE TABLE IF NOT EXISTS chats (
    id          TEXT PRIMARY KEY,
    transport   TEXT NOT NULL,
    chat_type   TEXT NOT NULL DEFAULT 'private',
    title       TEXT,
    provider    TEXT,
    model       TEXT,
    persona     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);

CREATE TABLE IF NOT EXISTS providers (
    name        TEXT PRIMARY KEY,
    base_url    TEXT NOT NULL,
    api_key     TEXT,
    model       TEXT,
    vision_model TEXT,
    extra       TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT
);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id     TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    PRIMARY KEY (chat_id, key)
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    name        TEXT PRIMARY KEY,
    transport   TEXT NOT NULL,
    command     TEXT,
    args        TEXT,
    env         TEXT,
    url         TEXT,
    headers     TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT,
    scope       TEXT NOT NULL DEFAULT 'global',
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT,
    user_id     TEXT,
    tool        TEXT,
    args        TEXT,
    ok          INTEGER NOT NULL DEFAULT 1,
    ms          INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(id);

CREATE TABLE IF NOT EXISTS usage_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT,
    transport   TEXT,
    provider    TEXT,
    model       TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS authorized_groups (
    chat_id     TEXT PRIMARY KEY,
    title       TEXT,
    added_by    TEXT,
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kv (
    scope       TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    PRIMARY KEY (scope, key)
);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    transport   TEXT,
    user_id     TEXT,
    text        TEXT NOT NULL,
    due_ts      INTEGER NOT NULL,
    sent        INTEGER NOT NULL DEFAULT 0,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(sent, due_ts);

CREATE TABLE IF NOT EXISTS group_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    message_id  INTEGER NOT NULL,
    user_id     TEXT,
    sender_name TEXT,
    text        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_group_messages_chat ON group_messages(chat_id, id);

CREATE TABLE IF NOT EXISTS todos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    text        TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schedules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    transport   TEXT,
    prompt      TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    next_ts     INTEGER NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    user_id     TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sticker_cache (
    key         TEXT PRIMARY KEY,
    file_id     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'sticker',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS team_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'decision',
    content     TEXT NOT NULL,
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass(slots=True)
class ProviderRow:
    name: str
    base_url: str
    api_key: str
    model: str
    vision_model: str = ""
    extra: dict[str, Any] | None = None
    enabled: bool = True

    @property
    def normalized_base(self) -> str:
        base = (self.base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        return base


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA busy_timeout=5000",
            "PRAGMA foreign_keys=ON",
        ):
            await self._conn.execute(pragma)
        await self._conn.executescript(SCHEMA)
        try:
            await self._conn.execute("ALTER TABLE schedules ADD COLUMN user_id TEXT DEFAULT ''")
        except Exception:  # noqa: BLE001
            pass
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    # ---------------------------------------------------------------- helpers
    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            await self.conn.commit()
            return int(cur.lastrowid or 0)

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        async with self._lock:
            await self.conn.executemany(sql, [tuple(r) for r in rows])
            await self.conn.commit()

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            return list(await cur.fetchall())

    # ---------------------------------------------------------------- kv / settings
    async def kv_get(self, scope: str, key: str, default: str | None = None) -> str | None:
        row = await self.fetchone("SELECT value FROM kv WHERE scope=? AND key=?", (scope, key))
        return row["value"] if row else default

    async def kv_set(self, scope: str, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO kv (scope,key,value) VALUES (?,?,?) "
            "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value",
            (scope, key, value),
        )

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = await self.fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    async def chat_get(self, chat_id: str, key: str) -> str | None:
        row = await self.fetchone("SELECT value FROM chat_settings WHERE chat_id=? AND key=?", (chat_id, key))
        return row["value"] if row else None

    async def chat_set(self, chat_id: str, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO chat_settings (chat_id,key,value) VALUES (?,?,?) "
            "ON CONFLICT(chat_id,key) DO UPDATE SET value=excluded.value",
            (chat_id, key, value),
        )

    # ---------------------------------------------------------------- users/chats
    async def touch_user(self, user_id: int, username: str = "", first_name: str = "") -> str:
        row = await self.fetchone("SELECT role FROM users WHERE id=?", (str(user_id),))
        if row is None:
            await self.execute(
                "INSERT INTO users (id,username,first_name,last_seen) VALUES (?,?,?,datetime('now'))",
                (str(user_id), username, first_name),
            )
            return "user"
        await self.execute(
            "UPDATE users SET username=?, first_name=?, last_seen=datetime('now') WHERE id=?",
            (username, first_name, str(user_id)),
        )
        return str(row["role"])

    async def set_user_role(self, user_id: int, role: str) -> None:
        await self.execute("UPDATE users SET role=? WHERE id=?", (role, str(user_id)))

    async def get_user_role(self, user_id: int) -> str:
        row = await self.fetchone("SELECT role FROM users WHERE id=?", (str(user_id),))
        return str(row["role"]) if row else "user"

    async def ensure_chat(self, chat_id: int, transport: str, chat_type: str, title: str = "") -> None:
        await self.execute(
            "INSERT INTO chats (id,transport,chat_type,title) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, chat_type=excluded.chat_type",
            (str(chat_id), transport, chat_type, title),
        )

    async def chat_provider(self, chat_id: int) -> str | None:
        row = await self.fetchone("SELECT provider FROM chats WHERE id=?", (str(chat_id),))
        return row["provider"] if row else None

    async def chat_model(self, chat_id: int) -> str | None:
        row = await self.fetchone("SELECT model FROM chats WHERE id=?", (str(chat_id),))
        return row["model"] if row else None

    async def set_chat_route(self, chat_id: int, provider: str | None, model: str | None) -> None:
        await self.execute(
            "UPDATE chats SET provider=COALESCE(?,provider), model=COALESCE(?,model) WHERE id=?",
            (provider, model, str(chat_id)),
        )

    # ---------------------------------------------------------------- messages
    async def add_message(self, chat_id: str, role: str, content: str) -> None:
        await self.execute(
            "INSERT INTO messages (chat_id,role,content) VALUES (?,?,?)",
            (chat_id, role, content),
        )

    async def history(self, chat_id: str, limit: int = 20) -> list[dict[str, str]]:
        rows = await self.fetchall(
            "SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )
        return [{"role": r["role"], "content": r["content"] or ""} for r in reversed(rows)]

    async def clear_history(self, chat_id: str) -> int:
        async with self._lock:
            cur = await self.conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            await self.conn.commit()
            return cur.rowcount or 0

    # ---------------------------------------------------------------- providers
    async def save_provider(
        self,
        name: str,
        base_url: str,
        api_key_enc: str,
        model: str,
        vision_model: str = "",
        extra: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> None:
        await self.execute(
            "INSERT INTO providers (name,base_url,api_key,model,vision_model,extra,enabled) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "base_url=excluded.base_url, api_key=excluded.api_key, model=excluded.model, "
            "vision_model=excluded.vision_model, extra=excluded.extra, enabled=excluded.enabled",
            (name, base_url, api_key_enc, model, vision_model, json.dumps(extra or {}), int(enabled)),
        )

    async def get_provider(self, name: str) -> ProviderRow | None:
        row = await self.fetchone("SELECT * FROM providers WHERE name=?", (name,))
        if row is None:
            return None
        return ProviderRow(
            name=row["name"],
            base_url=row["base_url"],
            api_key=row["api_key"] or "",
            model=row["model"] or "",
            vision_model=row["vision_model"] or "",
            extra=json.loads(row["extra"]) if row["extra"] else {},
            enabled=bool(row["enabled"]),
        )

    async def list_providers(self) -> list[ProviderRow]:
        rows = await self.fetchall("SELECT * FROM providers ORDER BY name")
        return [
            ProviderRow(
                name=r["name"],
                base_url=r["base_url"],
                api_key=r["api_key"] or "",
                model=r["model"] or "",
                vision_model=r["vision_model"] or "",
                extra=json.loads(r["extra"]) if r["extra"] else {},
                enabled=bool(r["enabled"]),
            )
            for r in rows
        ]

    async def delete_provider(self, name: str) -> None:
        await self.execute("DELETE FROM providers WHERE name=?", (name,))
        await self.execute("UPDATE chats SET provider=NULL WHERE provider=?", (name,))

    async def default_provider(self) -> str | None:
        return await self.get_setting("default_provider")

    # ---------------------------------------------------------------- mcp
    async def save_mcp(
        self,
        name: str,
        transport: str,
        command: str = "",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str = "",
        headers: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> None:
        await self.execute(
            "INSERT INTO mcp_servers (name,transport,command,args,env,url,headers,enabled) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "transport=excluded.transport, command=excluded.command, args=excluded.args, "
            "env=excluded.env, url=excluded.url, headers=excluded.headers, enabled=excluded.enabled",
            (
                name,
                transport,
                command,
                json.dumps(args or []),
                json.dumps(env or {}),
                url,
                json.dumps(headers or {}),
                int(enabled),
            ),
        )

    async def list_mcp(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mcp_servers" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY name"
        rows = await self.fetchall(sql)
        return [
            {
                "name": r["name"],
                "transport": r["transport"],
                "command": r["command"] or "",
                "args": json.loads(r["args"]) if r["args"] else [],
                "env": json.loads(r["env"]) if r["env"] else {},
                "url": r["url"] or "",
                "headers": json.loads(r["headers"]) if r["headers"] else {},
                "enabled": bool(r["enabled"]),
            }
            for r in rows
        ]

    async def delete_mcp(self, name: str) -> None:
        await self.execute("DELETE FROM mcp_servers WHERE name=?", (name,))

    # ---------------------------------------------------------------- reminders
    async def add_reminder(self, chat_id: str | int, transport: str, user_id: str | int, text: str, due_ts: int) -> int:
        return await self.execute(
            "INSERT INTO reminders (chat_id,transport,user_id,text,due_ts) VALUES (?,?,?,?,?)",
            (str(chat_id), transport, str(user_id), text, int(due_ts)),
        )

    async def list_reminders(
        self, chat_id: str | int | None = None, include_done: bool = False, limit: int = 50
    ) -> list[aiosqlite.Row]:
        where = ["1=1"]
        params: list[object] = []
        if chat_id is not None:
            where.append("chat_id=?")
            params.append(str(chat_id))
        if not include_done:
            where.append("sent=0")
        return await self.fetchall(
            f"SELECT * FROM reminders WHERE {' AND '.join(where)} ORDER BY due_ts LIMIT ?",
            (*params, limit),
        )

    async def due_reminders(self, now_ts: int, limit: int = 20) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM reminders WHERE sent=0 AND due_ts<=? AND attempts<3 ORDER BY due_ts LIMIT ?",
            (int(now_ts), limit),
        )

    async def mark_reminder_sent(self, reminder_id: int) -> None:
        await self.execute("UPDATE reminders SET sent=1 WHERE id=?", (int(reminder_id),))

    async def mark_reminder_failed(self, reminder_id: int, error: str) -> None:
        await self.execute(
            "UPDATE reminders SET attempts=attempts+1, last_error=? WHERE id=?",
            (error[:500], int(reminder_id)),
        )

    async def delete_reminder(self, reminder_id: int, chat_id: str | int | None = None) -> bool:
        if chat_id is None:
            await self.execute("DELETE FROM reminders WHERE id=?", (int(reminder_id),))
            return True
        row = await self.fetchone(
            "SELECT id FROM reminders WHERE id=? AND chat_id=?", (int(reminder_id), str(chat_id))
        )
        if row is None:
            return False
        await self.execute("DELETE FROM reminders WHERE id=?", (int(reminder_id),))
        return True

    async def pending_reminders_count(self, chat_id: str | int | None = None) -> int:
        if chat_id is None:
            row = await self.fetchone("SELECT COUNT(*) AS n FROM reminders WHERE sent=0")
        else:
            row = await self.fetchone("SELECT COUNT(*) AS n FROM reminders WHERE sent=0 AND chat_id=?", (str(chat_id),))
        return int(row["n"]) if row else 0

    # ---------------------------------------------------------------- group history
    async def add_group_message(
        self, chat_id: str | int, message_id: int, user_id: int | str, sender_name: str, text: str
    ) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO group_messages (chat_id,message_id,user_id,sender_name,text) VALUES (?,?,?,?,?)",
            (str(chat_id), int(message_id), str(user_id), sender_name[:120], (text or "")[:4000]),
        )

    async def group_history(self, chat_id: str | int, limit: int = 100) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM group_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit),
        )

    async def search_group(self, chat_id: str | int, query: str, limit: int = 20) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM group_messages WHERE chat_id=? AND text LIKE ? ORDER BY id DESC LIMIT ?",
            (str(chat_id), f"%{query}%", limit),
        )

    async def group_user_history(self, chat_id: str | int, user: str, limit: int = 30) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM group_messages WHERE chat_id=? AND (user_id=? OR sender_name LIKE ?) "
            "ORDER BY id DESC LIMIT ?",
            (str(chat_id), str(user), f"%{user}%", limit),
        )

    # ---------------------------------------------------------------- todos / schedules
    async def add_todo(self, chat_id: str | int, text: str) -> int:
        return await self.execute("INSERT INTO todos (chat_id,text) VALUES (?,?)", (str(chat_id), text[:1000]))

    async def list_todos(self, chat_id: str | int, include_done: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM todos WHERE chat_id=?" + ("" if include_done else " AND done=0") + " ORDER BY id"
        return await self.fetchall(sql, (str(chat_id),))

    async def complete_todo(self, chat_id: str | int, todo_id: int) -> bool:
        row = await self.fetchone("SELECT id FROM todos WHERE id=? AND chat_id=?", (int(todo_id), str(chat_id)))
        if row is None:
            return False
        await self.execute("UPDATE todos SET done=1 WHERE id=?", (int(todo_id),))
        return True

    async def clear_todos(self, chat_id: str | int) -> None:
        await self.execute("DELETE FROM todos WHERE chat_id=?", (str(chat_id),))

    async def add_schedule(self, chat_id: str | int, transport: str, prompt: str, interval_seconds: int, user_id: str | int = "") -> int:
        now = int(time.time())
        return await self.execute(
            "INSERT INTO schedules (chat_id,transport,prompt,interval_seconds,next_ts,user_id) VALUES (?,?,?,?,?,?)",
            (str(chat_id), transport, prompt[:1000], int(interval_seconds), now + int(interval_seconds), str(user_id)),
        )

    async def list_schedules(self, chat_id: str | int) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM schedules WHERE chat_id=? AND enabled=1 ORDER BY id", (str(chat_id),)
        )

    async def delete_schedule(self, chat_id: str | int, schedule_id: int) -> bool:
        row = await self.fetchone(
            "SELECT id FROM schedules WHERE id=? AND chat_id=?", (int(schedule_id), str(chat_id))
        )
        if row is None:
            return False
        await self.execute("DELETE FROM schedules WHERE id=?", (int(schedule_id),))
        return True

    async def due_schedules(self, now_ts: int, limit: int = 20) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM schedules WHERE enabled=1 AND next_ts<=? ORDER BY next_ts LIMIT ?",
            (int(now_ts), limit),
        )

    async def advance_schedule(self, schedule_id: int, next_ts: int) -> None:
        await self.execute("UPDATE schedules SET next_ts=? WHERE id=?", (int(next_ts), int(schedule_id)))

    async def search_messages(self, chat_id: str | int, query: str, limit: int = 20) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT role, content, created_at FROM messages WHERE chat_id=? AND content LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (str(chat_id), f"%{query}%", limit),
        )

    # ---------------------------------------------------------------- sticker cache
    async def save_sticker_file(self, key: str, file_id: str, kind: str = "sticker") -> None:
        await self.execute(
            "INSERT INTO sticker_cache (key,file_id,kind) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET file_id=excluded.file_id, kind=excluded.kind",
            (key.strip()[:64], file_id, kind),
        )

    async def get_sticker_file(self, key: str, kind: str | None = None) -> str:
        if kind:
            row = await self.fetchone("SELECT file_id FROM sticker_cache WHERE key=? AND kind=?", (key, kind))
        else:
            row = await self.fetchone("SELECT file_id FROM sticker_cache WHERE key=?", (key,))
        return str(row["file_id"]) if row else ""

    async def list_sticker_files(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT key,kind,created_at FROM sticker_cache ORDER BY key")

    # ---------------------------------------------------------------- user memory / team notes
    async def add_user_memory(self, user_id: str | int, content: str) -> int:
        return await self.execute(
            "INSERT INTO user_memories (user_id,content) VALUES (?,?)", (str(user_id), content[:2000])
        )

    async def list_user_memories(self, user_id: str | int, limit: int = 30) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM user_memories WHERE user_id=? ORDER BY id DESC LIMIT ?", (str(user_id), limit)
        )

    async def delete_user_memory(self, user_id: str | int, mem_id: int) -> None:
        await self.execute("DELETE FROM user_memories WHERE id=? AND user_id=?", (mem_id, str(user_id)))

    async def add_team_note(self, chat_id: str | int, kind: str, content: str, created_by: str | int) -> int:
        return await self.execute(
            "INSERT INTO team_notes (chat_id,kind,content,created_by) VALUES (?,?,?,?)",
            (str(chat_id), kind[:20], content[:2000], str(created_by)),
        )

    async def list_team_notes(self, chat_id: str | int, limit: int = 50) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM team_notes WHERE chat_id=? ORDER BY id DESC LIMIT ?", (str(chat_id), limit)
        )

    async def search_team_notes(self, chat_id: str | int, query: str, limit: int = 20) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM team_notes WHERE chat_id=? AND content LIKE ? ORDER BY id DESC LIMIT ?",
            (str(chat_id), f"%{query}%", limit),
        )

    # ---------------------------------------------------------------- memories
    async def add_memory(self, chat_id: str | None, content: str, scope: str = "global") -> int:
        return await self.execute(
            "INSERT INTO memories (chat_id,scope,content) VALUES (?,?,?)",
            (chat_id, scope, content),
        )

    async def list_memories(self, chat_id: str | None = None, limit: int = 50) -> list[aiosqlite.Row]:
        if chat_id is None:
            return await self.fetchall(
                "SELECT id,chat_id,scope,content,created_at FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            )
        return await self.fetchall(
            "SELECT id,chat_id,scope,content,created_at FROM memories "
            "WHERE chat_id=? OR scope='global' ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )

    async def delete_memory(self, mem_id: int) -> None:
        await self.execute("DELETE FROM memories WHERE id=?", (mem_id,))

    # ---------------------------------------------------------------- groups
    async def authorize_group(self, chat_id: int, title: str, added_by: int) -> None:
        await self.execute(
            "INSERT INTO authorized_groups (chat_id,title,added_by) VALUES (?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
            (str(chat_id), title, str(added_by)),
        )

    async def is_group_authorized(self, chat_id: int) -> bool:
        row = await self.fetchone("SELECT 1 FROM authorized_groups WHERE chat_id=?", (str(chat_id),))
        return row is not None

    async def list_groups(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM authorized_groups ORDER BY added_at DESC")

    async def remove_group(self, chat_id: int) -> None:
        await self.execute("DELETE FROM authorized_groups WHERE chat_id=?", (str(chat_id),))

    # ---------------------------------------------------------------- audit / usage
    async def audit(
        self,
        chat_id: str,
        user_id: str,
        tool: str,
        args: str,
        ok: bool,
        ms: int,
        error: str = "",
    ) -> None:
        await self.execute(
            "INSERT INTO audit (chat_id,user_id,tool,args,ok,ms,error) VALUES (?,?,?,?,?,?,?)",
            (chat_id, user_id, tool, args[:2000], int(ok), ms, error[:2000]),
        )

    async def recent_audit(self, limit: int = 10) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    async def add_usage(
        self,
        chat_id: str,
        transport: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        await self.execute(
            "INSERT INTO usage_log (chat_id,transport,provider,model,prompt_tokens,completion_tokens) "
            "VALUES (?,?,?,?,?,?)",
            (chat_id, transport, provider, model, prompt_tokens, completion_tokens),
        )

    async def usage_totals(self) -> dict[str, int]:
        row = await self.fetchone(
            "SELECT COALESCE(SUM(prompt_tokens),0) AS p, COALESCE(SUM(completion_tokens),0) AS c, "
            "COUNT(*) AS n FROM usage_log"
        )
        return {"prompt": int(row["p"]), "completion": int(row["c"]), "calls": int(row["n"])} if row else {
            "prompt": 0,
            "completion": 0,
            "calls": 0,
        }

    async def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("users", "chats", "messages", "providers", "mcp_servers", "memories", "authorized_groups"):
            row = await self.fetchone(f"SELECT COUNT(*) AS n FROM {table}")
            out[table] = int(row["n"]) if row else 0
        return out

    async def purge_old_messages(self, keep_per_chat: int = 500) -> int:
        removed = 0
        chats = await self.fetchall("SELECT DISTINCT chat_id FROM messages")
        for row in chats:
            async with self._lock:
                cur = await self.conn.execute(
                    "DELETE FROM messages WHERE chat_id=? AND id NOT IN "
                    "(SELECT id FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?)",
                    (row["chat_id"], row["chat_id"], keep_per_chat),
                )
                removed += cur.rowcount or 0
                await self.conn.commit()
        return removed


def utcnow_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
