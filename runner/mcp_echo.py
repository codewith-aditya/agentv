"""Minimal MCP stdio server used by Agent V self-tests (MCP SDK 2.x).

Run standalone:  python mcp_echo.py
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer("agentv-echo")


@server.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@server.tool()
def workspace_pwd() -> str:
    """Return the working directory of the MCP server process."""
    import os

    return os.getcwd()


if __name__ == "__main__":
    server.run()
