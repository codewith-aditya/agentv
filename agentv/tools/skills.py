"""Let the agent create its own plugins/skills at runtime.

A "skill" is just a plugin file with a `setup(api)` function: it can register
new tools and commands, which become available on the next model turn.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Tool, ToolContext, ToolResult

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,48}$")

SKILL_TEMPLATE = '''"""Auto-created skill: {description}"""

from agentv.tools.base import Tool, ToolContext, ToolResult


async def _handler(ctx: ToolContext, query: str = "") -> ToolResult:
    return ToolResult("replace me: " + query)


def setup(api) -> None:
    api.register_tool(
        Tool(
            name="{name}_tool",
            description="Describe what this tool does for the model.",
            parameters={{
                "type": "object",
                "properties": {{"query": {{"type": "string"}}}},
                "required": ["query"],
            }},
            handler=_handler,
            min_role="user",
            source="plugin:{name}",
        )
    )
'''


def _plugins_dir(ctx: ToolContext) -> Path:
    plugins = getattr(ctx, "plugins", None)
    if plugins is not None:
        return Path(plugins.plugin_dir)
    return Path(ctx.cfg.data_path.parent / "plugins")


async def _create_skill(
    ctx: ToolContext,
    name: str = "",
    description: str = "",
    code: str = "",
    overwrite: bool = False,
) -> ToolResult:
    if ctx.role != "owner":
        return ToolResult("Only the owner can create skills (they run inside the bot process).", is_error=True)
    slug = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not SLUG_RE.match(slug):
        return ToolResult("name must be snake_case, 2-49 chars, starting with a letter.", is_error=True)
    code = (code or "").strip()
    if not code:
        return ToolResult(
            "code is required. It must define `def setup(api)` and may use "
            "`from agentv.tools.base import Tool, ToolContext, ToolResult`.\n\nTemplate:\n"
            + SKILL_TEMPLATE.format(name=slug, description=description or "custom tool"),
            is_error=True,
        )
    if "def setup(" not in code:
        return ToolResult(
            "code must define `def setup(api)`. Use this template:\n\n"
            + SKILL_TEMPLATE.format(name=slug, description=description or "custom tool"),
            is_error=True,
        )
    try:
        compile(code, f"{slug}.py", "exec")
    except SyntaxError as exc:
        return ToolResult(f"SyntaxError in skill code: line {exc.lineno}: {exc.msg}", is_error=True)

    path = _plugins_dir(ctx) / f"{slug}.py"
    if path.exists() and not overwrite:
        return ToolResult(f"skill `{slug}` already exists — pass overwrite=true to replace it.", is_error=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"# {description}\n" if description and not code.lstrip().startswith('"""') else ""
    path.write_text(header + code + "\n", encoding="utf-8")

    infos = ctx.plugins.reload() if getattr(ctx, "plugins", None) is not None else []
    info = next((p for p in infos if p.name == slug), None)
    if info is None:
        return ToolResult(f"skill `{slug}` written but not loaded (plugins reload unavailable).", is_error=True)
    if info.error:
        return ToolResult(f"skill `{slug}` written but failed to load: {info.error}", is_error=True)
    return ToolResult(
        f"✅ skill `{slug}` created · tools: {', '.join(info.tools) or 'none'} · "
        f"commands: {', '.join(info.commands) or 'none'}"
    )


async def _list_skills(ctx: ToolContext) -> ToolResult:
    plugins = getattr(ctx, "plugins", None)
    if plugins is None:
        return ToolResult("plugins are not available", is_error=True)
    infos = list(plugins.plugins.values())
    if not infos:
        return ToolResult("No skills/plugins loaded.")
    lines = []
    for info in infos:
        state = f"error: {info.error}" if info.error else f"tools={len(info.tools)} commands={len(info.commands)}"
        lines.append(f"- {info.name} v{info.version or '?'} · {state}")
    return ToolResult("\n".join(lines))


async def _delete_skill(ctx: ToolContext, name: str = "") -> ToolResult:
    if ctx.role != "owner":
        return ToolResult("Only the owner can delete skills.", is_error=True)
    slug = (name or "").strip().lower().replace("-", "_")
    path = _plugins_dir(ctx) / f"{slug}.py"
    if not path.exists():
        return ToolResult(f"skill `{slug}` not found.", is_error=True)
    path.unlink()
    if getattr(ctx, "plugins", None) is not None:
        ctx.plugins.reload()
    return ToolResult(f"🗑 skill `{slug}` deleted and plugins reloaded.")


async def _install_skill(ctx: ToolContext, url: str = "", name: str = "") -> ToolResult:
    """One-tap install: fetch a plugin .py from a URL and hot-reload it."""
    if ctx.role != "owner":
        return ToolResult("Only the owner can install skills.", is_error=True)
    if not url.startswith(("http://", "https://")):
        return ToolResult("http(s) url to a plugin .py is required", is_error=True)
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            return ToolResult(f"download failed: HTTP {resp.status_code}", is_error=True)
        code = resp.text
        if "def setup(" not in code:
            return ToolResult("downloaded file has no setup(api); not a plugin", is_error=True)
        slug = (name or Path(url.split("?")[0]).stem or "skill").lower().replace("-", "_")
        slug = re.sub(r"[^a-z0-9_]", "_", slug)[:40] or "skill"
        path = _plugins_dir(ctx) / f"{slug}.py"
        path.write_text(code, encoding="utf-8")
        infos = ctx.plugins.reload() if getattr(ctx, "plugins", None) is not None else []
        info = next((p for p in infos if p.name == slug), None)
        if info and info.error:
            return ToolResult(f"installed but failed to load: {info.error}", is_error=True)
        return ToolResult(f"✅ skill `{slug}` installed · tools: {', '.join(info.tools) if info else 'unknown'}")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"install failed: {exc}", is_error=True)


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="create_skill",
            description=(
                "Create a new plugin/skill at runtime: write a Python file with `def setup(api)` that "
                "registers new tools/commands, then hot-reload it. Owner only — this runs in the bot process."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "snake_case skill name"},
                    "description": {"type": "string"},
                    "code": {"type": "string", "description": "full Python source with setup(api)"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["name", "code"],
            },
            handler=_create_skill,
            min_role="owner",
        ),
        Tool(
            name="list_skills",
            description="List loaded plugins/skills with their tools and commands.",
            parameters={"type": "object", "properties": {}},
            handler=_list_skills,
            min_role="user",
        ),
        Tool(
            name="delete_skill",
            description="Delete a runtime-created skill by name and reload plugins (owner only).",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            handler=_delete_skill,
            min_role="owner",
        ),
        Tool(
            name="install_skill",
            description="One-tap skill install: download a plugin .py from a URL and hot-reload it (owner only).",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}, "name": {"type": "string"}},
                "required": ["url"],
            },
            handler=_install_skill,
            min_role="owner",
        ),
    ]
