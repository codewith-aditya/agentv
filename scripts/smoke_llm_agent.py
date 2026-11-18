#!/usr/bin/env python3
"""Real end-to-end smoke: actual model + tool calling + Docker sandbox.

    LLM_API_KEY=sk-... .venv/bin/python scripts/smoke_llm_agent.py

Prints the model's final answer and the audit rows so you can see the tool
actually ran. Exits non-zero if the model never called the shell tool.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentv.commands import CommandRouter  # noqa: E402
from agentv.config import Settings  # noqa: E402
from agentv.core import AgentCore  # noqa: E402
from agentv.crypto import SecretBox  # noqa: E402
from agentv.db import Database  # noqa: E402
from agentv.llm import LLMClient  # noqa: E402
from agentv.mcp_hub import MCPHub  # noqa: E402
from agentv.plugins_loader import PluginManager  # noqa: E402
from agentv.sandbox import create_sandbox  # noqa: E402
from agentv.tools import build_registry  # noqa: E402
from agentv.types import Incoming, SentMessage  # noqa: E402


class Capture:
    transport = "smoke"
    telegram_ops = None

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str, reply_to: int | None = None) -> SentMessage:
        self.sent.append(text)
        return SentMessage("smoke", 0, len(self.sent))

    async def edit(self, handle, text: str) -> None:
        pass

    async def delete(self, handle) -> None:
        pass

    async def send_file(self, path, caption: str = "", reply_to: int | None = None) -> None:
        self.sent.append(f"[file] {path}")

    async def typing(self, on: bool = True) -> None:
        pass

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        return False


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="agentv-smoke-"))
    cfg = Settings(
        owner_ids="999",
        data_dir=str(tmp / "data"),
        database_path=str(tmp / "data" / "agentv.db"),
        plugins_dir=str(ROOT / "plugins"),
        sandbox_enabled=True,
        log_level="INFO",
    )
    if not cfg.llm_api_key:
        print("LLM_API_KEY missing")
        return 2
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    await db.connect()
    box = SecretBox("smoke-secret")
    await db.save_provider("env", cfg.llm_base_url, box.encrypt(cfg.llm_api_key), cfg.llm_model, extra={"max_tokens": 1500})
    await db.set_setting("default_provider", "env")

    sandbox = await create_sandbox(cfg)
    registry = build_registry(db)
    hub = MCPHub(cfg, db, sandbox)
    plugins = PluginManager(cfg, db, registry, CommandRouter())
    llm = LLMClient(cfg)
    core = AgentCore(cfg, db, llm, sandbox, registry, hub, plugins, box)
    plugins.router = core.router
    await core.start()

    inc = Incoming(
        transport="smoke",
        chat_id=777,
        chat_type="private",
        user_id=999,
        text=(
            "Run exactly this command with the run_shell tool: "
            "python3 -c 'import uuid; print(uuid.uuid4())'\n"
            "Then reply with only the UUID that the command printed. "
            "Do not guess it — it must come from the tool output."
        ),
        message_id=1,
    )
    cap = Capture()
    await core.handle_message(inc, cap)
    final = "\n".join(cap.sent)
    print("\n--- final message ---")
    print(final[-2000:])

    rows = await db.fetchall("SELECT tool, args, ok, ms, error FROM audit WHERE chat_id='777' ORDER BY id")
    print("\n--- audit ---")
    for r in rows:
        print(dict(r))

    await core.stop()
    await db.close()
    import re

    has_uuid = bool(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", final))
    if not rows:
        print("\nFAIL: model made no tool calls (min_role/tool schema/gateway issue)")
        return 1
    if not has_uuid:
        print("\nFAIL: final answer does not contain a real UUID from the tool")
        return 1
    print("\nPASS: real model called a tool and used its output")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
