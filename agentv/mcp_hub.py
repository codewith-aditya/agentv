from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .config import Settings
from .db import Database
from .sandbox import BaseSandbox, DockerSandbox
from .tools.base import Tool, ToolContext, ToolRegistry, ToolResult

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _safe(name: str, limit: int = 48) -> str:
    return NAME_RE.sub("_", name).strip("_")[:limit] or "tool"


@dataclass
class MCPConnection:
    name: str
    stack: AsyncExitStack
    session: ClientSession
    tools: list[Any] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    error: str = ""


class MCPHub:
    """Connects configured MCP servers and exposes their tools to the agent."""

    def __init__(self, cfg: Settings, db: Database, sandbox: BaseSandbox):
        self.cfg = cfg
        self.db = db
        self.sandbox = sandbox
        self._conns: dict[str, MCPConnection] = {}
        self._errors: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        servers = await self.db.list_mcp(enabled_only=True)
        if not servers:
            return
        await asyncio.gather(*(self._connect_safe(s) for s in servers), return_exceptions=True)

    async def close(self) -> None:
        async with self._lock:
            conns = list(self._conns.values())
            self._conns.clear()
        for conn in conns:
            try:
                await asyncio.shield(asyncio.wait_for(conn.stack.aclose(), timeout=10))
            except Exception:  # noqa: BLE001
                log.debug("mcp close failed for %s", conn.name, exc_info=True)

    async def reload(self) -> None:
        await self.close()
        await self.start()

    # ------------------------------------------------------------------ connect
    async def _connect_safe(self, server: dict[str, Any]) -> None:
        try:
            await self._connect(server)
        except Exception as exc:  # noqa: BLE001
            self._errors[server["name"]] = f"{type(exc).__name__}: {exc}"
            log.warning("mcp server %s failed: %s", server["name"], exc)

    async def _connect(self, server: dict[str, Any]) -> MCPConnection:
        name = server["name"]
        transport = server["transport"]
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            if transport == "stdio":
                params = await self._stdio_params(server)
                streams = await stack.enter_async_context(stdio_client(params))
                read, write = self._streams(streams)
            elif transport == "sse":
                streams = await stack.enter_async_context(
                    sse_client(server["url"], headers=server.get("headers") or {})
                )
                read, write = self._streams(streams)
            elif transport in ("http", "streamable_http"):
                from mcp.shared._httpx_utils import create_mcp_http_client

                headers = {str(k): str(v) for k, v in (server.get("headers") or {}).items()}
                http_client = create_mcp_http_client(headers=headers or None)
                await stack.enter_async_context(http_client)
                streams = await stack.enter_async_context(
                    streamable_http_client(server["url"], http_client=http_client)
                )
                read, write = self._streams(streams)
            else:
                raise ValueError(f"unsupported transport: {transport}")

            session = await stack.enter_async_context(
                ClientSession(read, write, read_timeout_seconds=60.0)
            )
            await asyncio.wait_for(session.initialize(), timeout=30)
            listed = await asyncio.wait_for(session.list_tools(), timeout=30)
            tools = list(getattr(listed, "tools", []) or [])
            conn = MCPConnection(name=name, stack=stack, session=session, tools=tools)
            self._conns[name] = conn
            self._errors.pop(name, None)
            log.info("mcp server %s connected (%d tools)", name, len(tools))
            return conn
        except Exception:
            try:
                await stack.aclose()
            except Exception:  # noqa: BLE001
                pass
            raise

    @staticmethod
    def _streams(streams: Any) -> tuple[Any, Any]:
        if isinstance(streams, tuple):
            return streams[0], streams[1]
        read = getattr(streams, "read_stream", None) or getattr(streams, "read", None)
        write = getattr(streams, "write_stream", None) or getattr(streams, "write", None)
        if read is None or write is None:
            raise RuntimeError(f"unexpected MCP transport streams: {type(streams)!r}")
        return read, write

    async def _stdio_params(self, server: dict[str, Any]) -> StdioServerParameters:
        command = server.get("command") or ""
        args = list(server.get("args") or [])
        env = {str(k): str(v) for k, v in (server.get("env") or {}).items()}
        if not command:
            raise ValueError("stdio server needs a command")

        if isinstance(self.sandbox, DockerSandbox):
            container = await self.sandbox.container_name()
            if not container:
                raise RuntimeError("sandbox container is not available")
            docker_args = ["exec", "-i"]
            for key, value in env.items():
                docker_args += ["-e", f"{key}={value}"]
            docker_args += [container, command, *args]
            return StdioServerParameters(command="docker", args=docker_args, env=None)

        merged = {**os.environ, **env}
        return StdioServerParameters(command=command, args=args, env=merged)

    # ------------------------------------------------------------------ tools
    def server_summary(self) -> list[dict[str, Any]]:
        out = []
        for name, conn in self._conns.items():
            out.append({"name": name, "tools": len(conn.tools), "error": ""})
        for name, err in self._errors.items():
            out.append({"name": name, "tools": 0, "error": err})
        return out

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {"server": name, "tool": t.name, "description": (getattr(t, "description", "") or "")[:200]}
            for name, conn in self._conns.items()
            for t in conn.tools
        ]

    async def register_tools(self, registry: ToolRegistry) -> int:
        registry.unregister_source("mcp")
        count = 0
        for server_name, conn in list(self._conns.items()):
            for mcp_tool in conn.tools:
                tool_name = f"mcp_{_safe(server_name, 20)}_{_safe(mcp_tool.name, 40)}"
                if registry.get(tool_name):
                    tool_name = f"{tool_name[:56]}_{hashlib.md5(server_name.encode()).hexdigest()[:6]}"
                schema = getattr(mcp_tool, "inputSchema", None) or {"type": "object", "properties": {}}
                description = (
                    f"[MCP:{server_name}] " + (getattr(mcp_tool, "description", "") or mcp_tool.name)
                )[:1000]

                def make_handler(srv: str, tl: str):
                    async def handler(ctx: ToolContext, **kwargs: Any) -> ToolResult:
                        try:
                            text = await self.call_tool(srv, tl, kwargs, timeout=self.cfg.tool_timeout_seconds)
                        except Exception as exc:  # noqa: BLE001
                            return ToolResult(f"MCP call failed ({srv}/{tl}): {exc}", is_error=True)
                        return ToolResult(text)

                    return handler

                registry.register(
                    Tool(
                        name=tool_name,
                        description=description,
                        parameters=schema,
                        handler=make_handler(server_name, mcp_tool.name),
                        min_role="owner",
                        source="mcp",
                    )
                )
                count += 1
        return count

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any], timeout: int = 120
    ) -> str:
        conn = self._conns.get(server)
        if conn is None:
            raise RuntimeError(f"MCP server '{server}' is not connected")
        async with conn.lock:
            result = await asyncio.wait_for(conn.session.call_tool(tool, arguments=arguments or {}), timeout=timeout)
        texts: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                texts.append(str(text))
            else:
                data = getattr(item, "data", None)
                if data:
                    texts.append(f"[{type(item).__name__}] {str(data)[:1000]}")
        if getattr(result, "isError", False):
            raise RuntimeError("\n".join(texts) or "MCP tool returned an error")
        return "\n".join(texts) or "(no output)"

    # ------------------------------------------------------------------ management
    async def add_server(self, server: dict[str, Any]) -> None:
        await self.db.save_mcp(
            name=server["name"],
            transport=server["transport"],
            command=server.get("command", ""),
            args=server.get("args"),
            env=server.get("env"),
            url=server.get("url", ""),
            headers=server.get("headers"),
        )
        await self._connect_safe(await self._server_row(server["name"]))

    async def _server_row(self, name: str) -> dict[str, Any]:
        for row in await self.db.list_mcp():
            if row["name"] == name:
                return row
        raise KeyError(name)

    async def remove_server(self, name: str) -> None:
        conn = self._conns.pop(name, None)
        if conn is not None:
            try:
                await asyncio.wait_for(conn.stack.aclose(), timeout=10)
            except Exception:  # noqa: BLE001
                pass
        self._errors.pop(name, None)
        await self.db.delete_mcp(name)

    def health(self) -> dict[str, Any]:
        return {"servers": self.server_summary(), "tools": len(self.tool_specs())}
