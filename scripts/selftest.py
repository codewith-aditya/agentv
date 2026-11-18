#!/usr/bin/env python3
"""Agent V self-test — exercises DB, render, sandbox, MCP, plugin loading,
the tool-calling agent loop, and (optionally) a real LLM call.

    .venv/bin/python scripts/selftest.py            # everything available
    .venv/bin/python scripts/selftest.py --skip-llm --skip-docker
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentv.commands import CommandRouter  # noqa: E402
from agentv.config import Settings  # noqa: E402
from agentv.core import AgentCore  # noqa: E402
from agentv.crypto import SecretBox  # noqa: E402
from agentv.db import Database, ProviderRow  # noqa: E402
from agentv.llm import LLMClient, LLMError, LLMResult, ToolCall  # noqa: E402
from agentv.mcp_hub import MCPHub  # noqa: E402
from agentv.plugins_loader import PluginManager  # noqa: E402
from agentv.render import chunk_markdown, md_to_html  # noqa: E402
from agentv.rich import thinking_details, thinking_quote, to_rich_html  # noqa: E402
from agentv.sandbox import DockerSandbox, create_sandbox  # noqa: E402
from agentv.scheduler import Notifier, ReminderScheduler  # noqa: E402
from agentv.tools import build_registry  # noqa: E402
from agentv.tools.base import ToolContext  # noqa: E402
from agentv.tools.reminders import parse_when  # noqa: E402
from agentv.types import Incoming, SentMessage  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
        print(f"  ✅ {name}" + (f" · {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  ❌ {name}" + (f" · {detail}" if detail else ""))


class FakeResponder:
    transport = "fake"
    telegram_ops = None

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.files: list[Path] = []
        self.edits = 0
        self.edit_texts: list[str] = []

    async def send(self, text: str, reply_to: int | None = None) -> SentMessage:
        self.sent.append(text)
        return SentMessage("fake", 0, len(self.sent))

    async def edit(self, handle: SentMessage | None, text: str) -> None:
        self.edits += 1
        self.edit_texts.append(text)

    async def delete(self, handle: SentMessage | None) -> None:
        pass

    async def send_file(self, path: Path | str, caption: str = "", reply_to: int | None = None) -> None:
        self.files.append(Path(path))

    async def typing(self, on: bool = True) -> None:
        pass

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        return False


class FakeRichResponder(FakeResponder):
    supports_rich = True
    supports_drafts = False
    supports_expandable = True
    tool_log_ttl = 10

    def __init__(self) -> None:
        super().__init__()
        self.drafts: list[str] = []
        self.edits: list[str] = []

    async def draft_update(self, html_text: str) -> bool:
        self.drafts.append(html_text)
        return True

    async def send_rich(self, html_text: str, reply_to: int | None = None) -> SentMessage:
        self.sent.append("[rich] " + html_text)
        return SentMessage("fake", 0, len(self.sent))

    async def edit_rich(self, handle: SentMessage | None, html_text: str) -> bool:
        self.edits.append(html_text)
        return True


class FakeLLM:
    """Deterministic: first call asks for a shell tool, second answers."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, provider, messages, tools=None, model=None, temperature=0.4, on_delta=None, on_reasoning=None, reasoning_effort=None):
        self.calls += 1
        if self.calls % 2 == 1:
            return LLMResult(
                tool_calls=[ToolCall("call_1", "run_shell", json.dumps({"command": "echo hello-agentv"}))],
                finish_reason="tool_calls",
            )
        text = "done: hello-agentv"
        if on_delta:
            await on_delta(text)
        return LLMResult(content=text, finish_reason="stop", reasoning="checked the echo output")

    async def list_models(self, provider):
        return ["fake-model"]


class FakeBot:
    """Minimal stand-in for aiogram.Bot to check method arguments offline."""

    class _Msg:
        message_id = 4242

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send_rich_message(self, **kwargs):
        self.calls.append(("send_rich_message", kwargs))
        return self._Msg()

    async def send_rich_message_draft(self, **kwargs):
        self.calls.append(("send_rich_message_draft", kwargs))
        return True

    async def send_message_draft(self, **kwargs):
        self.calls.append(("send_message_draft", kwargs))
        return True

    async def send_message(self, *args, **kwargs):
        self.calls.append(("send_message", kwargs))
        return self._Msg()

    async def edit_message_text(self, **kwargs):
        self.calls.append(("edit_message_text", kwargs))
        return self._Msg()


async def test_bot_responder(cfg: Settings) -> None:
    print("\n[bot responder]")
    from agentv.transports.botapi import BotResponder, Caps

    fake = FakeBot()
    resp = BotResponder(fake, 12345, None, cfg.model_copy(update={"draft_streaming": "on"}), Caps())
    ok = await resp.draft_update("<tg-thinking>Thinking…</tg-thinking>")
    call = fake.calls[-1] if fake.calls else ("", {})
    check("draft method called", ok and call[0] == "send_rich_message_draft")
    check(
        "draft payload",
        call[1].get("draft_id", 0) != 0
        and "tg-thinking" in getattr(call[1].get("rich_message"), "html", "")
        and call[1].get("can_stop") is True,
    )
    sent = await resp.send_rich("<h1>Hi</h1><table bordered striped compact><tr><td>1</td></tr></table>")
    call = fake.calls[-1]
    check("sendRichMessage called", sent is not None and call[0] == "send_rich_message")
    check("rich input html", getattr(call[1].get("rich_message"), "html", "").startswith("<h1>Hi</h1>"))
    group = BotResponder(fake, -100123, None, cfg, Caps())
    check("drafts skipped in groups", not await group.draft_update("<tg-thinking>x</tg-thinking>"))
    disabled = BotResponder(fake, 12345, None, cfg.model_copy(update={"rich_messages": "off"}), Caps())
    check("rich off respected", await disabled.send_rich("<p>x</p>") is None and disabled.supports_rich is False)
    edited = await resp.edit_rich(SentMessage("fake", 12345, 777), "<details><summary>x</summary><p>y</p></details>")
    call = fake.calls[-1]
    check("editMessageText rich used", edited and call[0] == "edit_message_text" and "rich_message" in call[1])


SKILL_CODE = '''
from agentv.tools.base import Tool, ToolContext, ToolResult


async def _hello(ctx: ToolContext, name: str = "world") -> ToolResult:
    return ToolResult(f"hello {name}")


def setup(api) -> None:
    api.register_tool(
        Tool(
            name="selftest_hello",
            description="Say hello (created at runtime by the agent).",
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=_hello,
            min_role="user",
            source="plugin:selftest_skill",
        )
    )
'''


async def test_reminders(core: AgentCore, db: Database, cfg: Settings) -> None:
    print("\n[reminders]")
    check("parse 10m", parse_when("10m") is not None)
    check("parse 1h30m", parse_when("1h30m") is not None)
    check("parse 14:30", parse_when("14:30") is not None)
    check("parse junk", parse_when("whenever") is None)

    ctx = ToolContext(
        cfg=cfg,
        db=db,
        sandbox=core.sandbox,
        chat_id=4243,
        chat_type="private",
        user_id=next(iter(cfg.owners)),
        role="owner",
        transport="fake",
        plugins=core.plugins,
    )
    res = await core.registry.execute("set_reminder", {"text": "drink water", "when": "10m"}, ctx)
    check("set_reminder tool", not res.is_error and "Reminder #" in res.content, res.content[:80])
    listed = await core.registry.execute("list_reminders", {}, ctx)
    check("list_reminders tool", "drink water" in listed.content)

    due_id = await db.add_reminder(4243, "fake", 999, "due right now", int(time.time()) - 5)

    class FakeNotifier:
        def __init__(self) -> None:
            self.sent: list[tuple[int, str]] = []

        async def send(self, chat_id, text, preferred=None):
            self.sent.append((chat_id, text))
            return True

    notifier = FakeNotifier()
    scheduler = ReminderScheduler(db, notifier)  # type: ignore[arg-type]
    delivered = await scheduler.tick()
    check("scheduler delivered due", delivered >= 1, str(delivered))
    check("reminder text delivered", any("due right now" in text for _, text in notifier.sent))
    row = await db.fetchone("SELECT sent FROM reminders WHERE id=?", (due_id,))
    check("reminder marked sent", row is not None and row["sent"] == 1)


async def test_skills(core: AgentCore, db: Database, cfg: Settings) -> None:
    print("\n[skills]")
    ctx = ToolContext(
        cfg=cfg,
        db=db,
        sandbox=core.sandbox,
        chat_id=4244,
        chat_type="private",
        user_id=next(iter(cfg.owners)),
        role="owner",
        transport="fake",
        plugins=core.plugins,
    )
    res = await core.registry.execute(
        "create_skill",
        {"name": "selftest_skill", "description": "self-test skill", "code": SKILL_CODE},
        ctx,
    )
    check("create_skill tool", not res.is_error, res.content[:120])
    check("skill tool registered", core.registry.get("selftest_hello") is not None)
    called = await core.registry.execute("selftest_hello", {"name": "agentv"}, ctx)
    check("skill tool runs", called.content == "hello agentv", called.content[:60])
    removed = await core.registry.execute("delete_skill", {"name": "selftest_skill"}, ctx)
    check("delete_skill tool", not removed.is_error and core.registry.get("selftest_hello") is None)


def test_rich() -> None:
    print("\n[rich]")
    html_text = to_rich_html("# Title\n\n**bold** and `code`\n\n- one\n- two\n\n| A | B |\n|---|---|\n| 1 | 2 |")
    check("rich heading", "<h1>Title</h1>" in html_text)
    check("rich bold/code", "<b>bold</b>" in html_text and "<code>code</code>" in html_text)
    check("rich list", "<ul><li>one</li><li>two</li></ul>" in html_text)
    check("rich table", "<table bordered striped compact>" in html_text and "<th>A</th>" in html_text and "<td>1</td>" in html_text)
    code = to_rich_html("```python\nprint('<x>')\n```")
    check("rich code fence", '<pre><code class="language-python">' in code and "&lt;x&gt;" in code)
    tasks = to_rich_html("- [x] done\n- [ ] todo")
    check("rich task list", 'type="checkbox" checked' in tasks and 'type="checkbox">' in tasks)
    quote = to_rich_html("> quoted line one\n> line two")
    check("rich blockquote expandable", "<blockquote expandable>" in quote and "line two" in quote)
    math = to_rich_html("$$E = mc^2$$\n\ninline $x^2$")
    check("rich math", "<tg-math-block>E = mc^2</tg-math-block>" in math and "<tg-math>x^2</tg-math>" in math)
    details = thinking_details("step one\nstep two")
    check("thinking details", "<details><summary>🧠 Thinking</summary>" in details and "step one" in details)
    check("thinking quote fallback", "<blockquote expandable>🧠" in thinking_quote("hmm", True))
    spoiler = to_rich_html("||secret||")
    check("rich spoiler", "<tg-spoiler>secret</tg-spoiler>" in spoiler)
    from agentv.rich import activity_details

    activity = activity_details(
        [{"name": "run_shell", "args": {"command": "echo hi"}, "output": "hi", "ok": True}]
    )
    check("activity details block", "⚙️ Code execution" in activity and 'class="language-bash"' in activity and "echo hi" in activity)
    from agentv.core import auto_reaction

    check("auto react thanks", auto_reaction("thank you bro") == "🙏")
    check("auto react funny", auto_reaction("haha lol") == "😂")
    check("auto react photo", auto_reaction("", "photo") == "🔥")


def test_render() -> None:
    print("\n[render]")
    out = md_to_html("**bold** and `a<b` and <script>alert(1)</script>")
    check("bold rendered", "<b>bold</b>" in out)
    check("inline code escaped", "<code>a&lt;b</code>" in out)
    check("raw html neutralised", "<script>" not in out)
    fenced = md_to_html("```python\nprint('<x>')\n```")
    check("fence -> pre/code", "<pre><code class=\"language-python\">" in fenced and "&lt;x&gt;" in fenced)
    long = "paragraph line\n\n" * 500
    chunks = chunk_markdown(long, 1000)
    check("chunking splits", len(chunks) > 1, f"{len(chunks)} chunks")
    check("chunk size bounded", all(len(c) <= 2000 for c in chunks))
    mixed = md_to_html("```\nopen fence\n```\n```\nsecond\n```")
    check("multiple fences", mixed.count("<pre>") == 2)


async def test_db(tmp: Path) -> Database:
    print("\n[database]")
    db = Database(tmp / "test.db")
    await db.connect()
    await db.touch_user(999, "owner", "Owner")
    await db.ensure_chat(-100123, "bot", "supergroup", "Test")
    await db.add_message("-100123", "user", "hi")
    await db.add_message("-100123", "assistant", "hello")
    history = await db.history("-100123", 10)
    check("history roundtrip", [h["role"] for h in history] == ["user", "assistant"])
    box = SecretBox("secret-token")
    enc = box.encrypt("sk-test-123")
    check("secret encrypted", enc != "sk-test-123" and box.decrypt(enc) == "sk-test-123")
    check("secret mask", SecretBox.mask("sk-test-123").startswith("sk-t"))
    await db.save_provider("p1", "https://example.com/v1", enc, "m1", extra={"max_tokens": 900})
    row = await db.get_provider("p1")
    check("provider roundtrip", row is not None and row.model == "m1" and row.extra["max_tokens"] == 900)
    await db.set_setting("default_provider", "p1")
    check("default provider", await db.default_provider() == "p1")
    await db.add_memory(None, "owner likes Hinglish", "global")
    mems = await db.list_memories("-100123")
    check("memories visible", any("Hinglish" in m["content"] for m in mems))
    await db.authorize_group(-100123, "Test", 999)
    check("group authorized", await db.is_group_authorized(-100123))
    await db.add_usage("-100123", "bot", "p1", "m1", 10, 5)
    usage = await db.usage_totals()
    check("usage totals", usage["prompt"] == 10 and usage["completion"] == 5)
    return db


async def test_sandbox(cfg: Settings):
    print("\n[sandbox]")
    started = time.monotonic()
    sandbox = await create_sandbox(cfg)
    check("sandbox created", sandbox is not None, f"{sandbox.backend} in {time.monotonic() - started:.1f}s")
    res = await sandbox.exec(999, "echo agentv-ok && python3 -c 'print(6*7)' > /dev/null && echo py-ok", timeout=60)
    check("exec echo", "agentv-ok" in res.stdout, res.combined(200))
    check("python in sandbox", "py-ok" in res.stdout, f"exit={res.exit_code}")
    await sandbox.write_file(999, "nested/hello.txt", b"hi there")
    data = await sandbox.read_file(999, "nested/hello.txt")
    check("file write/read", data == b"hi there")
    export = getattr(sandbox, "export_file", None)
    path = await export(999, "nested/hello.txt") if export else sandbox.export_path(999, "nested/hello.txt")
    check("file export", Path(path).exists() and Path(path).read_text() == "hi there")
    slow = await sandbox.exec(999, "sleep 10", timeout=2)
    check("timeout kills", slow.timed_out, f"{slow.duration:.1f}s exit={slow.exit_code}")
    if isinstance(sandbox, DockerSandbox):
        name = await sandbox.container_name()
        check("runner container", bool(name), str(name))
    return sandbox


async def test_mcp(core: AgentCore, sandbox) -> None:
    print("\n[mcp]")
    servers = core.mcp.server_summary()
    if not servers:
        check("mcp server connected", False, "no server configured")
        return
    info = servers[0]
    check("mcp server connected", not info["error"], f"{info['name']}: {info['tools']} tools, err={info['error']}")
    if info["error"]:
        return
    text = ""
    for spec in core.mcp.tool_specs():
        if spec["tool"] == "add":
            text = await core.mcp.call_tool(info["name"], "add", {"a": 2, "b": 3})
    check("mcp tool call", text.strip() == "5", text.strip()[:80])
    check("mcp tools registered", any(t.name.startswith(f"mcp_{info['name']}_") for t in core.registry.all()))


async def test_agent_loop(core: AgentCore, db: Database, cfg: Settings) -> None:
    print("\n[agent loop]")
    fake = FakeLLM()
    core.llm = fake  # type: ignore[assignment]
    await db.save_provider("fake", "http://fake/v1", "", "fake-model")
    await db.set_setting("default_provider", "fake")
    inc = Incoming(
        transport="fake",
        chat_id=4242,
        chat_type="private",
        user_id=next(iter(cfg.owners)),
        text="run echo hello-agentv",
        message_id=1,
    )
    resp = FakeResponder()
    await core.handle_message(inc, resp)
    joined = "\n".join(resp.sent + resp.edit_texts)
    check("final answer delivered", "done: hello-agentv" in joined, joined[-200:])
    rows = await db.fetchall("SELECT tool, ok FROM audit WHERE chat_id='4242' ORDER BY id DESC")
    check("tool executed + audited", any(r["tool"] == "run_shell" and r["ok"] for r in rows), str([dict(r) for r in rows][:3]))
    check("two llm calls", fake.calls == 2, str(fake.calls))
    check("history persisted", len(await db.history("4242", 10)) == 2)

    # second turn: final is edited into the live status message (single message)
    rich = FakeRichResponder()
    inc.text = "again"
    await core.handle_message(inc, rich)
    check("draft frames not used", len(rich.drafts) == 0)
    check(
        "final edited in place",
        any("done: hello-agentv" in t for t in rich.edits + rich.sent),
    )
    check("tool activity block", any("Code execution" in t for t in rich.edits + rich.sent))
    check(
        "no duplicate custom thinking block",
        sum(1 for t in rich.sent if "🧠 Thinking" in t) == 0
        and "Thinking" not in (rich.edits[0] if rich.edits else ""),
    )

    await db.chat_set("4242", "stream", "off")
    quiet = FakeRichResponder()
    inc.text = "quiet"
    await core.handle_message(inc, quiet)
    check("stream off: no drafts", len(quiet.drafts) == 0 and any("done" in t for t in quiet.sent))
    await db.chat_set("4242", "stream", "on")


async def test_isolation(core: AgentCore, db: Database, cfg: Settings) -> None:
    print("\n[isolation]")
    a = ToolContext(cfg=cfg, db=db, sandbox=core.sandbox, chat_id=-100777, chat_type="supergroup",
                    user_id=111, workspace_id="-100777_111", role="user")
    b = ToolContext(cfg=cfg, db=db, sandbox=core.sandbox, chat_id=-100777, chat_type="supergroup",
                    user_id=222, workspace_id="-100777_222", role="user")
    await core.registry.execute("write_file", {"path": "private.txt", "content": "user-a"}, a)
    read_b = await core.registry.execute("read_file", {"path": "private.txt"}, b)
    check("group workspaces isolated", read_b.is_error and "not found" in read_b.content.lower())
    trav = await core.registry.execute("read_file", {"path": "../-100777_111/private.txt"}, b)
    check("workspace traversal blocked", trav.is_error)
    own = await core.registry.execute("read_file", {"path": "private.txt"}, a)
    check("own workspace readable", own.content == "user-a")


async def test_new_tools(core: AgentCore, db: Database, cfg: Settings) -> None:
    print("\n[new tools]")
    ctx = ToolContext(
        cfg=cfg, db=db, sandbox=core.sandbox, chat_id=5150, chat_type="private",
        user_id=999, role="owner", transport="fake", plugins=core.plugins, responder=None,
    )
    await core.registry.execute("write_file", {"path": "notes.txt", "content": "alpha line\nbeta line\n"}, ctx)
    found = await core.registry.execute("search_files", {"pattern": "beta"}, ctx)
    check("search_files", not found.is_error and "beta line" in found.content, found.content[:80])
    edited = await core.registry.execute(
        "edit_file", {"path": "notes.txt", "old": "alpha", "new": "gamma"}, ctx
    )
    check("edit_file", not edited.is_error and "edited" in edited.content, edited.content[:80])
    read = await core.registry.execute("read_file", {"path": "notes.txt"}, ctx)
    check("edit persisted", "gamma line" in read.content)

    added = await core.registry.execute("todo_list", {"action": "add", "text": "ship bot"}, ctx)
    listed = await core.registry.execute("todo_list", {"action": "list"}, ctx)
    check("todo add/list", "ship bot" in listed.content and "#" in added.content)

    await db.add_message("5150", "user", "remember the secret word pineapple")
    hist = await core.registry.execute("session_search", {"query": "pineapple"}, ctx)
    check("session_search", not hist.is_error and "pineapple" in hist.content)

    sid = await db.add_schedule(5150, "fake", "daily ping", 60)
    await db.execute("UPDATE schedules SET next_ts=? WHERE id=?", (int(time.time()) - 1, sid))
    class N:
        def __init__(self): self.sent = []
        async def send(self, chat_id, text, preferred=None):
            self.sent.append(text); return True
    from agentv.scheduler import ReminderScheduler
    n = N(); sch = ReminderScheduler(db, n)  # type: ignore[arg-type]
    delivered = await sch.tick()
    check("schedule_task delivered", delivered >= 1 and any("daily ping" in t for t in n.sent), str(delivered))

    proc = await core.registry.execute("process_manage", {"action": "list"}, ctx)
    check("process_manage list", not proc.is_error and len(proc.content) > 10)

    await db.save_sticker_file("😂", "FILEID123", "sticker")
    check("sticker cache save/get", await db.get_sticker_file("😂", "sticker") == "FILEID123")

    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (30, 80, 160)).save(buf, "PNG")
    await core.sandbox.write_file(ctx.ws_id, "pic.png", buf.getvalue())
    meme = await core.registry.execute("make_sticker", {"path": "pic.png", "text": "test meme"}, ctx)
    check("make_sticker", not meme.is_error and bool(meme.files) and meme.files[0].exists(), meme.content[:80])


async def test_commands(core: AgentCore, db: Database, cfg: Settings) -> None:
    print("\n[commands]")
    owner = next(iter(cfg.owners))

    async def run(text: str, user_id: int = owner) -> tuple[bool, str]:
        inc = Incoming(
            transport="fake",
            chat_id=4242,
            chat_type="private",
            user_id=user_id,
            text=text,
            message_id=9,
        )
        resp = FakeResponder()
        handled = await core.handle_command(inc, resp)
        return handled, "\n".join(resp.sent)

    handled, out = await run("/status")
    check("/status handled", handled and "<b>Status</b>" in out, out[:80])
    handled, out = await run("/api list")
    check("/api list", handled and "Providers" in out and "fake" in out)
    handled, out = await run("/mcp list")
    check("/mcp list", handled and "MCP" in out and "echo" in out)
    handled, out = await run("/uptime")
    check("plugin command /uptime", handled and "uptime" in out)
    handled, out = await run("/stream off")
    check("/stream off", handled and "stream: <code>off</code>" in out)
    handled, out = await run("/stream")
    check("/stream state", handled and "off" in out)
    await run("/stream on")
    handled, out = await run("/reasoning instant")
    check("/reasoning set", handled and "instant" in out and "no reasoning" in out)
    current = await core.db.chat_get("4242", "reasoning")
    check("/reasoning persisted", current == "instant")
    check("reasoning isolated per chat", await core.db.chat_get("-100999", "reasoning") is None)
    await run("/reasoning high")
    handled, _ = await run("/definitely-not-a-command")
    check("unknown command falls through", not handled)
    handled, out = await run("/api list", user_id=1234)
    check("owner-only command denied", handled and "Unauthorized" in out)
    group_inc = Incoming(
        transport="fake", chat_id=-100555, chat_type="supergroup", user_id=owner, text="/api list", message_id=10
    )
    group_resp = FakeResponder()
    handled = await core.handle_command(group_inc, group_resp)
    out = "\n".join(group_resp.sent + group_resp.edit_texts)
    check("admin commands blocked in groups", handled and "Private only" in out and "Providers" not in out)


async def test_real_llm(cfg: Settings) -> None:
    print("\n[real llm]")
    if not cfg.llm_api_key:
        check("llm key present", False, "LLM_API_KEY / MODELROUTER_API_KEY missing")
        return
    llm = LLMClient(cfg)
    provider = ProviderRow("env", cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model, extra={"max_tokens": 900})
    try:
        started = time.monotonic()
        result = await llm.chat(provider, [{"role": "user", "content": "Reply with exactly: pong"}], temperature=0)
        check("non-stream chat", "pong" in result.content.lower(), f"{time.monotonic() - started:.1f}s → {result.content[:60]!r}")
        streamed = ""

        async def on_delta(text: str) -> None:
            nonlocal streamed
            streamed = text

        result2 = await llm.chat(
            provider, [{"role": "user", "content": "count from 1 to 5, comma separated"}], on_delta=on_delta
        )
        check("streaming chat", "1" in streamed and len(streamed) >= len(result2.content) - 5, f"{len(streamed)} chars")
        models = await llm.list_models(provider)
        check("models endpoint", len(models) > 0, f"{len(models)} models")
    except LLMError as exc:
        check("real llm reachable", False, str(exc)[:200])
    finally:
        await llm.close()


async def amain(args: argparse.Namespace) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="agentv-selftest-"))
    plugins_dir = tmp / "plugins"
    shutil.copytree(ROOT / "plugins", plugins_dir)
    cfg = Settings(
        owner_ids="999",
        data_dir=str(tmp / "data"),
        database_path=str(tmp / "data" / "agentv.db"),
        plugins_dir=str(plugins_dir),
        sandbox_enabled=not args.skip_docker,
        sandbox_image="agentv-runner:latest",
        sandbox_network=True,
        log_level="WARNING",
        max_tool_steps=4,
    )
    cfg.ensure_dirs()
    print(f"workspace: {tmp}")

    test_render()
    test_rich()
    await test_bot_responder(cfg)
    db = await test_db(tmp)

    sandbox = await test_sandbox(cfg)

    mcp_script = (
        "/opt/agentv/mcp_echo.py"
        if isinstance(sandbox, DockerSandbox)
        else str(ROOT / "runner" / "mcp_echo.py")
    )
    mcp_command = "python3" if isinstance(sandbox, DockerSandbox) else sys.executable
    await db.save_mcp("echo", "stdio", command=mcp_command, args=[mcp_script])
    await db.save_provider("fake", "http://fake/v1", "", "fake-model")

    registry = build_registry(db)
    hub = MCPHub(cfg, db, sandbox)
    plugins = PluginManager(cfg, db, registry, CommandRouter())
    core = AgentCore(cfg, db, LLMClient(cfg), sandbox, registry, hub, plugins, SecretBox("selftest-secret"))
    plugins.router = core.router
    await core.start()

    print("\n[plugins]")
    check("plugins loaded", len(core.plugins.plugins) >= 1, ", ".join(core.plugins.plugins))
    example = core.plugins.plugins.get("example_tools")
    check("example plugin tools", bool(example and not example.error and example.tools), str(example))

    await test_mcp(core, sandbox)
    await test_agent_loop(core, db, cfg)
    await test_isolation(core, db, cfg)
    await test_new_tools(core, db, cfg)
    await test_commands(core, db, cfg)
    await test_reminders(core, db, cfg)
    await test_skills(core, db, cfg)
    if not args.skip_llm:
        await test_real_llm(cfg)

    await core.stop()
    await db.close()

    print("\n" + "=" * 60)
    print(f"PASS {len(PASS)} · FAIL {len(FAIL)}")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-llm", action="store_true", help="skip the real model call")
    parser.add_argument("--skip-docker", action="store_true", help="use LocalSandbox (dev only)")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(amain(args)))
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)


if __name__ == "__main__":
    main()
