from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .commands import CommandRouter, CommandSpec
from .config import Settings
from .db import Database
from .tools.base import Tool, ToolRegistry

log = logging.getLogger(__name__)

MessageHook = Callable[[Any, Any], Awaitable[None]]


@dataclass
class PluginInfo:
    name: str
    path: str
    version: str = ""
    description: str = ""
    tools: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    error: str = ""


class PluginAPI:
    """Surface a plugin sees: tools, commands, hooks and isolated KV storage."""

    def __init__(self, name: str, cfg: Settings, db: Database, registry: ToolRegistry, router: CommandRouter):
        self.name = name
        self.cfg = cfg
        self.db = db
        self.registry = registry
        self.router = router
        self.log = logging.getLogger(f"plugin.{name}")
        self._tools: list[str] = []
        self._commands: list[str] = []

    def register_tool(self, tool: Tool) -> Tool:
        tool.source = f"plugin:{self.name}"
        self.registry.register(tool)
        self._tools.append(tool.name)
        return tool

    def add_command(
        self,
        name: str,
        description: str,
        handler: Callable[..., Awaitable[None]],
        min_role: str = "user",
        usage: str = "",
    ) -> CommandSpec:
        spec = self.router.register(
            CommandSpec(
                name=name,
                description=description,
                handler=handler,
                min_role=min_role,
                usage=usage,
                source=f"plugin:{self.name}",
            )
        )
        self._commands.append(spec.name)
        return spec

    def on_message(self, hook: MessageHook) -> None:
        self.registry  # keep reference alive
        _HOOKS.setdefault(self.name, []).append(hook)

    async def kv_get(self, key: str, default: str | None = None) -> str | None:
        return await self.db.kv_get(f"plugin:{self.name}", key, default)

    async def kv_set(self, key: str, value: str) -> None:
        await self.db.kv_set(f"plugin:{self.name}", key, value)


_HOOKS: dict[str, list[MessageHook]] = {}


class PluginManager:
    def __init__(self, cfg: Settings, db: Database, registry: ToolRegistry, router: CommandRouter):
        self.cfg = cfg
        self.db = db
        self.registry = registry
        self.router = router
        self.plugins: dict[str, PluginInfo] = {}
        self._modules: dict[str, Any] = {}

    @property
    def plugin_dir(self) -> Path:
        p = Path(self.cfg.plugins_dir)
        if not p.is_absolute():
            p = self.cfg.data_path.parent / p
        return p

    def load_all(self) -> list[PluginInfo]:
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.plugin_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            self._load(path)
        return list(self.plugins.values())

    def _load(self, path: Path) -> None:
        name = path.stem
        self.registry.unregister_source(f"plugin:{name}")
        self.router.unregister_source(f"plugin:{name}")
        _HOOKS.pop(name, None)
        api = PluginAPI(name, self.cfg, self.db, self.registry, self.router)
        try:
            mod_name = f"agentv_plugin_{name}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not create import spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise RuntimeError("plugin has no setup(api) function")
            setup(api)
            self._modules[name] = module
            self.plugins[name] = PluginInfo(
                name=name,
                path=str(path),
                version=str(getattr(module, "__version__", "")),
                description=str(getattr(module, "__doc__", "") or "").strip().split("\n")[0],
                tools=api._tools,
                commands=api._commands,
            )
            log.info("plugin %s loaded (%d tools, %d commands)", name, len(api._tools), len(api._commands))
        except Exception as exc:  # noqa: BLE001
            log.exception("plugin %s failed to load", name)
            self.plugins[name] = PluginInfo(name=name, path=str(path), error=f"{type(exc).__name__}: {exc}")

    def reload(self) -> list[PluginInfo]:
        for name in list(self.plugins):
            self.registry.unregister_source(f"plugin:{name}")
            self.router.unregister_source(f"plugin:{name}")
            _HOOKS.pop(name, None)
            sys.modules.pop(f"agentv_plugin_{name}", None)
        self.plugins.clear()
        return self.load_all()

    async def run_hooks(self, inc: Any, responder: Any) -> None:
        for name, hooks in list(_HOOKS.items()):
            for hook in hooks:
                try:
                    await hook(inc, responder)
                except Exception:  # noqa: BLE001
                    log.exception("plugin hook failed: %s", name)
