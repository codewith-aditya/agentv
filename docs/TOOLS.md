# Tools

AgentV exposes these tools to the model. Roles: `owner` > `admin` > `user`.

## Builtin

| Tool | Role | Scope | Description |
|---|---|---|---|
| `browser` | user | any | Real headless browser: action=read\|screenshot\|click\|fill. Use for JS-heavy pages, screenshots, and interactions. |
| `browser_read` | user | any | Read a JavaScript-rendered web page as text via a rendering proxy. |
| `cancel_reminder` | user | any | Cancel a reminder by id in this chat. |
| `chat_history` | user | group | Read the stored message history of the current group (most recent last). |
| `chat_settings` | user | any | Show the current reasoning level, streaming state, provider and model for this chat. |
| `create_skill` | owner | any | Create a new plugin/skill at runtime: write a Python file with `def setup(api)` that registers new tools/commands, then hot-reload it. Owner only — this runs in the bot process. |
| `delegate_task` | user | any | Ask a separate model call to solve a focused sub-task and return its answer. |
| `delete_file` | owner | any | Delete a file in the chat workspace (owner only). |
| `delete_skill` | owner | any | Delete a runtime-created skill by name and reload plugins (owner only). |
| `edit_file` | user | any | Targeted text replacement in a workspace file (old -> new), optionally all occurrences. |
| `forget` | admin | any | Delete a saved memory by id. |
| `install_skill` | owner | any | One-tap skill install: download a plugin .py from a URL and hot-reload it (owner only). |
| `list_dir` | user | any | List files in a workspace directory. |
| `list_reminders` | user | any | List scheduled reminders for this chat. |
| `list_skills` | user | any | List loaded plugins/skills with their tools and commands. |
| `make_sticker` | user | any | Turn an image in the workspace into a 512x512 Telegram sticker with a caption. |
| `memory_list` | user | any | List saved memories visible in this chat. |
| `pdf_extract` | user | any | Extract text from a PDF in the workspace. |
| `pin_message` | admin | group | Pin a message in the current group (bot must be admin). |
| `process_manage` | user | any | Start/list/kill background processes in the workspace, and show recent logs. |
| `react` | user | any | React to a message with any emoji, chosen by mood: 👍 agree, ❤️ love, 🔥 impressive, 😂 funny, 🤔 curious, 🎉 celebrate, 🙏 thanks, 👀 noticed. Defaults to the user's latest message. |
| `read_file` | user | any | Read a UTF-8 text file from the chat workspace (max 200 KB). |
| `remember` | user | any | Persist a durable fact/instruction. scope=global is owner-only, scope=chat is per-chat. |
| `run_python` | user | private | Write Python code to a file in the sandbox and execute it with python3. |
| `run_shell` | user | private | Execute a bash command inside the isolated sandbox container. The working directory is the per-chat workspace. Use it for files, python, node, git, ffmpeg, package installs. Returns stdout/stderr/exit code. |
| `schedule_task` | user | any | Create/list/delete recurring tasks: action=add\|list\|del, every=15m\|1h\|1d. |
| `search_chat` | user | group | Search stored messages in the current group by text. |
| `search_files` | user | any | Search text inside workspace files with ripgrep (pattern + optional glob). |
| `send_file` | user | any | Send a workspace file to the Telegram chat as an attachment. |
| `send_gif` | user | any | Send a GIF: cached key, or a .mp4/.gif file from the workspace. |
| `send_sticker` | user | any | Send a Telegram sticker: either an existing sticker file_id, or a .webp/.png/.tgs file from the workspace. |
| `session_search` | user | any | Search earlier messages in this chat for a keyword/phrase. |
| `set_reasoning` | user | any | Change the reasoning/thinking level for this chat: instant (no thinking), low, medium, high, max. |
| `set_reminder` | user | any | Schedule a reminder message for this chat. `when` accepts 10m, 1h30m, 14:30, or an ISO datetime. |
| `set_stream` | user | any | Enable or disable live streaming/thinking UI for this chat. |
| `team_brain` | user | group | Group memory: record/list/search decisions, follow-ups and facts for this group. |
| `text_to_speech` | user | any | Convert text to a voice message and send it (edge-tts). |
| `tg_chat_stats` | user | group | Get member counts and metadata for the current chat. |
| `tg_delete_message` | admin | any | Delete a message by id in the current chat. |
| `tg_leave_chat` | owner | group | Leave the current group (owner only). |
| `tg_lookup` | user | any | Look up a Telegram user/group/channel by @username, link or numeric id. |
| `tg_manage_user` | admin | group | Kick/ban/unban/mute/unmute a member of the current group (admin+). |
| `todo_list` | user | any | Manage this chat's todo list: action=add\|list\|done\|clear. |
| `unpin_message` | admin | group | Unpin a message (or the pinned message) in the current group. |
| `user_history` | user | group | Read stored messages from one user in the current group (name or numeric id). |
| `web_fetch` | user | any | Fetch a public web page and return its readable text. |
| `web_search` | user | any | Search the web (Brave) and return titles, URLs and snippets. |
| `write_file` | user | any | Create or overwrite a text file in the chat workspace. |
