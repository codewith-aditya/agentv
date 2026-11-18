# Why AgentV

AgentV is a Telegram-native AI agent you fully own: your bot token, your model
gateway, your server, your data. This page explains what the 66 tools do and why
that matters.

## Tool reference by category

### Code & OS

| Tool | What it does |
|---|---|
| `run_shell` | Run bash inside a hardened Docker sandbox (owner full, users isolated) |
| `run_python` | Write + execute Python in the sandbox |
| `process_manage` | Start, list, kill background processes; tail logs |
| `read_file`, `write_file`, `list_dir`, `delete_file` | Workspace file operations |
| `search_files` | ripgrep inside the workspace |
| `edit_file` | Targeted `old → new` replacement (no full rewrite) |
| `pdf_extract` | Pull text out of PDFs |

### Web & research

| Tool | What it does |
|---|---|
| `web_search` | Search the web (Brave, or keyless Bing fallback) |
| `web_fetch` | Read a public page as text (SSRF-guarded) |
| `browser` | Real headless Chromium: JS pages, 1920×1080 screenshots, click, fill |
| `browser_read` | Rendered page text via a rendering proxy |

### Media

| Tool | What it does |
|---|---|
| `send_sticker` | Send a sticker by cached emoji key, file_id or workspace file |
| `send_gif` | Send a GIF from cache or a workspace file |
| `make_sticker` | Turn any photo into a 512×512 captioned `.webp` meme |
| `text_to_speech` | Generate and send a voice message (edge-tts) |
| `react` | React to messages with a mood-appropriate emoji |

### Memory & context

| Tool | What it does |
|---|---|
| `remember` / `memory_list` / `forget` | Chat, global and cross-chat **user** memories |
| `session_search` | Search this chat's earlier messages |
| `todo_list` | Per-chat todo list |
| `team_brain` | Group decisions, follow-ups and facts |

### Groups

| Tool | What it does |
|---|---|
| `chat_history` / `search_chat` / `user_history` | Stored group messages and per-user history |
| `summarize` (command) | Summarize the group's stored history |
| `tg_chat_stats`, `tg_manage_user`, `tg_delete_message`, `tg_leave_chat` | Moderation and chat management |
| `pin_message` / `unpin_message` | Pin or unpin messages |

### Automation

| Tool | What it does |
|---|---|
| `set_reminder` / `list_reminders` / `cancel_reminder` | One-shot reminders |
| `schedule_task` | Recurring tasks that run a **full agent turn** (tools included) |
| `delegate_task` | Focused sub-task answered by a separate model call |

### Settings & extensibility

| Tool | What it does |
|---|---|
| `set_reasoning`, `set_stream`, `chat_settings` | Per-chat reasoning/streaming/provider settings |
| `create_skill`, `install_skill`, `list_skills`, `delete_skill` | Runtime skills: the bot can write or install new tools itself |
| MCP tools | Every configured MCP server's tools, namespaced `mcp_<server>_<tool>` |

## Why choose AgentV

1. **Ownership.** Your bot token, your OpenAI-compatible gateway, your server and
   your SQLite data. No subscription, no vendor lock-in.
2. **Real execution.** A hardened Docker sandbox runs code, not just chat — with
   timeouts, quotas and per-user isolation.
3. **Isolation by design.** Per-chat workspaces, per-user sub-workspaces in
   groups, per-user offline containers for shell, no root, no cross-user reads.
4. **Bot + userbot in one runtime.** A normal BotFather bot and a Telethon
   userbot share the same core and tools.
5. **Bring your own models.** Any OpenAI-compatible endpoint, per-chat provider
   and model routing, encrypted API keys.
6. **Extensible at runtime.** Plugins, MCP servers, and skills the model can
   write or install itself — hot-reloaded without a restart.
7. **Telegram-native UX.** Rich messages, native drafts, collapsed thinking,
   timers, flood-wait retries and self-destructing tool logs.
8. **Groups done properly.** Authorized groups, stored history, search,
   summaries, team brain, moderation and mood-based reactions.
9. **Open source (MIT).** Audit it, fork it, self-host it, build a product on it.
10. **Cheap.** A VPS plus your own model gateway — Mira-class capabilities
    without a SaaS bill.

> **One line:** Mira-class Telegram agent, but on your own server, with your own
> keys — MIT licensed.
