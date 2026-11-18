<p align="center">
  <img src="docs/assets/logo.svg" width="120" alt="AgentV logo"/>
</p>

<h1 align="center">AgentV</h1>

<p align="center">
  <b>Open-source, self-hosted Telegram AI agent</b><br/>
  Bot + userbot · Docker code sandbox · MCP · plugins · 66 tools
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-22d3ee?style=flat-square"/></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776ab?style=flat-square&logo=python&logoColor=white"/>
  <img alt="Docker" src="https://img.shields.io/badge/docker-compose-2496ed?style=flat-square&logo=docker&logoColor=white"/>
  <img alt="Telegram Bot API" src="https://img.shields.io/badge/Telegram%20Bot%20API-10.x-26a5e4?style=flat-square&logo=telegram&logoColor=white"/>
  <img alt="MCP" src="https://img.shields.io/badge/MCP-stdio%20%7C%20http%20%7C%20sse-8b5cf6?style=flat-square"/>
  <img alt="Tools" src="https://img.shields.io/badge/tools-66-34d399?style=flat-square"/>
</p>

---

AgentV is a production-grade AI agent that lives inside Telegram. Unlike hosted
assistants, **you own the stack**: your bot token, your model gateway, your
server, your data. It ships with a hardened Docker sandbox so the model can
actually run code, a plugin/skill system it can extend itself, MCP servers,
per-user workspace isolation, group intelligence and a streaming rich-message UI.

Built for people who want [Mira](https://mira.tg)-class capabilities on their
own infrastructure — without a SaaS bill or vendor lock-in.

## ✨ Features

| | |
|---|---|
| 🤖 **Bot + userbot** | aiogram bot and Telethon userbot in one process, shared core |
| 📦 **Real code sandbox** | hardened runner containers, per-chat/per-user workspaces, 4 GB quota, 3 GB RAM, 25 s tool cap |
| 🔑 **Bring your own API** | any OpenAI-compatible gateway, encrypted keys, per-chat provider/model routing |
| 🧠 **Reasoning control** | `/reasoning instant\|low\|medium\|high\|max`, 60 s thinking cap, 120 s turn timeout |
| 🛠 **66 tools** | shell, files, web, browser (Chromium), PDF, stickers, GIF, TTS, reminders, todos, schedules, team brain, delegation and more |
| 🧩 **Self-extending** | `create_skill` writes a plugin with `setup(api)` and hot-reloads it; `install_skill` installs from a URL |
| 🔌 **MCP** | stdio servers run *inside* the sandbox; http/sse remote servers; tools auto-registered |
| 👥 **Groups** | authorized groups, moderation, stored history, search, `/summarize`, pin/unpin, team brain, reactions |
| ⏰ **Scheduling** | one-shot reminders and recurring tasks that run a full agent turn |
| 🎨 **Telegram-native UI** | native drafts / rich messages, collapsed thinking, timers, flood-wait retries, self-destructing tool logs |
| 🔒 **Isolation** | per-chat workspaces, per-user group sub-workspaces, per-user shell containers, no root, no cross-user reads |
| 🕵️ **Private mode** | `/private on` — per-chat ephemeral mode with no stored history |

## 🆚 AgentV vs Mira

Mira is a polished hosted product; AgentV is the open-source, self-hosted
alternative. Both are Telegram-native agents — the difference is who owns the
infrastructure.

| Capability | AgentV | Mira |
|---|:---:|:---:|
| Self-hosted / open source | ✅ MIT | ❌ SaaS |
| Bot + userbot in one runtime | ✅ | ❌ bot only |
| Your own model gateway / keys | ✅ any OpenAI-compatible | ❌ GPT / Qwen via Cocoon |
| Real Docker code sandbox | ✅ | ❌ |
| MCP servers | ✅ stdio/http/sse | ✅ |
| Plugin / skill system | ✅ hot-reload, self-written | ✅ catalog |
| One-tap skill install from URL | ✅ | ✅ catalog |
| Group history, search, summaries | ✅ | ✅ |
| Team brain (decisions/follow-ups) | ✅ | ✅ |
| Reminders + recurring agent runs | ✅ | ✅ |
| Reactions by mood, stickers, meme generator | ✅ | ✅ |
| Voice replies (TTS) | ✅ edge-tts | ✅ |
| Browser automation | ✅ Playwright | ✅ |
| Private mode (no history) | ✅ per chat | ✅ private compute |
| TEE / confidential compute | ❌ | ✅ Cocoon |
| 1,000+ OAuth integrations | ❌ MCP/manual | ✅ |
| TON wallet / on-chain | ❌ | ✅ |
| Hosted mobile/web app | ❌ | ✅ |
| Price | **$0 + your VPS** | subscription |

## 🏗 Architecture

```text
Telegram ──► bot (aiogram)  ─┐
            userbot (Telethon)├──► AgentCore ──► LLM gateway (OpenAI-compatible)
                               │      │
                               │      ├── tools (48 builtin)
                               │      ├── MCP servers (stdio / http / sse)
                               │      ├── plugins + runtime skills
                               │      └── Docker sandbox (agentv-runner)
                               └──► SQLite (WAL): chats, providers, MCP, audit,
                                    usage, reminders, schedules, group history,
                                    team brain, sticker cache
```

- **One message, one turn**: streamed into a single Telegram message, then edited
  into the final rich answer (no duplicate replies).
- **Tool loop**: native OpenAI function calling, up to 8 steps, fully audited.
- **Sandbox**: owner commands run in a network-enabled runner; regular users get
  a **per-user offline container** with only their workspace mounted.

## 🚀 Quick start

```bash
git clone https://github.com/codewith-aditya/agentv.git
cd agentv
cp .env.example .env
# set OWNER_IDS, BOT_TOKEN, LLM_BASE_URL, LLM_API_KEY
docker compose up -d --build
docker logs -f agentv
```

First boot builds the `agentv-runner` sandbox image (~1–2 min). Health:

```bash
curl -s localhost:8080/healthz | jq
```

Bare metal:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m agentv
```

Verify everything before touching Telegram:

```bash
.venv/bin/python scripts/selftest.py              # 94 checks (Docker + optional LLM)
.venv/bin/python scripts/smoke_llm_agent.py       # real model → tool call → answer
```

## ⚙️ Configuration

Everything lives in `.env` (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `OWNER_IDS` | — | comma-separated numeric Telegram ids with full authority |
| `BOT_TOKEN` | — | BotFather token |
| `USERBOT_ENABLED` | `false` | Telethon as your account (`TG_API_ID`, `TG_API_HASH`, `USERBOT_SESSION`) |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | your gateway / your-model | default provider |
| `SANDBOX_MEMORY` / `SANDBOX_STORAGE` | `3g` / `4g` | runner limits |
| `TOOL_TIMEOUT_SECONDS` | `25` | hard per-tool cap |
| `THINKING_TIMEOUT_SECONDS` | `60` | max pure-thinking before a forced answer |
| `LLM_TIMEOUT` | `120` | overall model timeout |
| `MAX_UPLOAD_MB` / `MAX_DOWNLOAD_MB` | `10` / `50` | media limits |
| `AUTO_REACT` | `true` | mood-based automatic reactions |
| `RICH_MESSAGES` / `DRAFT_STREAMING` | `auto` / `on` | rich rendering / native drafts |
| `TOOL_LOG_TTL` | `10` | seconds before the tool log self-destructs (0 = off) |
| `RATE_LIMIT_PER_MIN` | `0` | per-user messages/minute (0 = off, owner exempt) |
| `ALLOW_USER_SHELL` | `true` | non-owner private shell in an isolated offline container |

## 💡 Why AgentV

- **Ownership** — your bot token, your model gateway, your server, your data.
  No subscription, no lock-in.
- **Real execution** — hardened Docker sandbox runs code with timeouts, quotas
  and per-user isolation, not just chat.
- **Isolation by design** — per-chat workspaces, per-user group sub-workspaces,
  per-user offline shell containers, no root, no cross-user reads.
- **Bot + userbot** — one runtime, shared tools and memory.
- **Bring your own models** — any OpenAI-compatible gateway, per-chat routing,
  encrypted keys.
- **Extensible at runtime** — plugins, MCP servers and skills the model can
  write or install itself, hot-reloaded without a restart.
- **Telegram-native UX** — rich messages, native drafts, collapsed thinking,
  timers, flood-wait retries, self-destructing tool logs.
- **Groups done properly** — history, search, summaries, team brain, moderation,
  reactions.
- **Open source (MIT)** — audit it, fork it, self-host it, build on it.
- **Cheap** — a VPS plus your own gateway; Mira-class capabilities without a
  SaaS bill.

Read the full breakdown: [`docs/WHY_AGENTV.md`](docs/WHY_AGENTV.md).

## 💬 Commands

Users:

```
/start /help /new /stop /status /id
/model /models /reasoning /stream
/remember /memories /forget
/remind 10m <text> /reminders /unremind <id>
/skills /private on|off
/summarize [N]        (groups only)
```

Owner (DM only):

```
/api list|add|del|default|test
/mcp list|add|del|reload
/plugins [reload] /persona <text> /shell <cmd>
/promote <user_id> [admin|user] /groups /approve
```

## 🧰 Tools

66 tools are exposed to the model (48 builtin + 1 plugin + 17 MCP in the default
setup): shell, Python, files, search/edit, PDF, web search/fetch, **Chromium
browser**, stickers/GIFs with file-id cache, **meme generator**, TTS, reminders,
todos, recurring schedules, group history/search/team brain, pin/unpin,
moderation, reactions, memory (chat/global/user), delegation, settings and
runtime skill management.

Full reference: [`docs/TOOLS.md`](docs/TOOLS.md) · why AgentV:
[`docs/WHY_AGENTV.md`](docs/WHY_AGENTV.md).

| Category | Tools |
|---|---|
| Code & OS | `run_shell`, `run_python`, `process_manage`, `search_files`, `edit_file`, file ops |
| Web | `web_search`, `web_fetch`, `browser`, `browser_read`, `pdf_extract` |
| Media | `send_sticker`, `send_gif`, `make_sticker`, `text_to_speech`, `react` |
| Memory | `remember`, `memory_list`, `forget`, `session_search`, `todo_list`, `team_brain` |
| Groups | `chat_history`, `search_chat`, `user_history`, moderation, pin/unpin, `/summarize` |
| Automation | reminders, `schedule_task`, `delegate_task`, `set_reasoning`, `set_stream` |
| Extensibility | `create_skill`, `install_skill`, plugins, MCP tools |

## 🔌 MCP

```bash
# stdio (runs inside the sandbox)
/mcp add filesystem stdio npx -y @modelcontextprotocol/server-filesystem /workspace

# remote
/mcp add {"name":"search","transport":"http","url":"https://mcp.example.com/mcp"}

/mcp list
/mcp reload
```

MCP tools become `mcp_<server>_<tool>` and are owner-only by default.

## 🧩 Plugins & skills

`plugins/example_tools.py` is a working template. The model can write new ones at
runtime with `create_skill` (owner only) and install one from a URL with
`install_skill(url)`. Both hot-reload — no restart. Plugins can register tools,
commands, message hooks and per-plugin KV storage.

## ⏰ Reminders & schedules

`/remind 10m stand up` for one-shot reminders. Recurring tasks use the
`schedule_task` tool (`15m`, `1h`, `1d`) and run a **full agent turn** — tools
included — then post the result in the chat.

## 🔒 Security model

- Owner identity is numeric Telegram ids only — never names or usernames.
- Groups must be authorized; the owner adding the bot auto-authorizes, anyone
  else gets the bot leaving.
- Per-chat workspaces; groups get per-member sub-workspaces; path traversal is
  rejected; global memories are never injected into other users' prompts.
- Non-owner shell runs in a **separate offline container** with only that user's
  directory mounted, uid 1000, `cap_drop=ALL`, `no-new-privileges`, pids/memory
  limits; `sudo`, `su`, `chroot`, `mount`, `docker`, `nsenter` are blocked.
- Provider keys are encrypted at rest with Fernet (`SECRET_KEY`).
- Every tool call is audited (tool, args, duration, result).

The bot container needs `/var/run/docker.sock` — root-equivalent on the host.
Keep bot code and plugins trustworthy.

## 🗺 Roadmap

- [ ] OAuth integrations (Gmail / Calendar / Notion / GitHub)
- [ ] Image & video generation
- [ ] Speech-to-text for voice notes
- [ ] PWA / web chat dashboard
- [ ] Skill marketplace index
- [ ] TEE deployment guide

## 🤝 Contributing

PRs are welcome. Run `scripts/selftest.py` before opening one — see
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## 📄 License

MIT — see [`LICENSE`](LICENSE).
