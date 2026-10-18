"""MCP servers: their settings (kept in mcp.json next to the saved discussions) and a live
connection to each, so their tools can be given to agents.

A server is either a command Moot starts (stdio, like Claude Desktop's config) or a URL
(Streamable HTTP). Each connection runs in its own task for as long as Moot runs, and tool
calls are queued to it. A server that fails to start is reported, never fatal.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from moot.tools.base import ToolError, ToolResult, ToolSpec

log = logging.getLogger(__name__)

MCP_FILE = "mcp.json"
CALL_TIMEOUT = 90.0
CONNECT_TIMEOUT = 60.0
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class McpServerConfig(BaseModel):
    """How to reach one MCP server."""

    command: str | None = Field(default=None, description="the program to start (stdio)")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict, description="extra environment variables")
    url: str | None = Field(default=None, description="a Streamable HTTP address instead")

    @field_validator("url")
    @classmethod
    def _http(cls, value: str | None) -> str | None:
        if value is not None and not re.match(r"^https?://", value.strip()):
            raise ValueError("the URL must start with http:// or https://")
        return value.strip() if value else None

    @model_validator(mode="after")
    def _one_way(self) -> McpServerConfig:
        if bool(self.command and self.command.strip()) == bool(self.url):
            raise ValueError("give either a command to start or a URL")
        return self


def check_name(name: str) -> str:
    if not NAME_PATTERN.match(name):
        raise ValueError(
            "server names use lower-case letters, digits, - and _ (up to 40), e.g. 'notion'"
        )
    return name


def load_configs(path: Path | None) -> dict[str, McpServerConfig]:
    """The servers in mcp.json ({"mcpServers": {name: {...}}}); a broken file is reported
    and treated as empty."""
    if path is None or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
        return {check_name(name): McpServerConfig.model_validate(c) for name, c in raw.items()}
    except (ValueError, AttributeError) as exc:
        log.error("can't read %s: %s", path, exc)
        return {}


def save_configs(path: Path | None, configs: dict[str, McpServerConfig]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "mcpServers": {
            name: c.model_dump(exclude_none=True, exclude_defaults=True)
            for name, c in configs.items()
        }
    }
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")


def tool_id(server: str, tool: str) -> str:
    return f"mcp:{server}:{tool}"


def result_text(result: Any) -> str:
    """The readable part of an MCP tool result."""
    parts = []
    for block in getattr(result, "content", []) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(block.text)
        elif kind == "resource" and getattr(getattr(block, "resource", None), "text", None):
            parts.append(block.resource.text)
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} not shown]")
        elif kind == "resource_link":
            parts.append(f"[link: {getattr(block, 'uri', '')}]")
    structured = getattr(result, "structured_content", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False)[:4000])
    return "\n".join(parts).strip() or "(no text in the result)"


@dataclass
class McpConnection:
    name: str
    config: McpServerConfig
    status: str = "connecting"  # connecting, connected, failed, stopped
    error: str | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    _calls: asyncio.Queue = field(default_factory=asyncio.Queue)
    _ready: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task | None = None

    def start(self, target: Any = None) -> None:
        self._task = asyncio.create_task(self._run(target), name=f"moot-mcp-{self.name}")

    async def _run(self, target: Any) -> None:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        if target is None:
            target = (
                self.config.url
                if self.config.url
                else StdioServerParameters(
                    command=self.config.command or "",
                    args=self.config.args,
                    env=self.config.env or None,
                )
            )
        try:
            async with Client(target, read_timeout_seconds=CALL_TIMEOUT) as client:
                tools, cursor = [], None
                while True:
                    listing = await client.list_tools(cursor=cursor)
                    tools += listing.tools
                    cursor = getattr(listing, "next_cursor", None)
                    if not cursor:
                        break
                self.tools = [
                    ToolSpec(
                        id=tool_id(self.name, t.name),
                        name=f"{self.name}.{t.name}",
                        description=(t.description or "").strip()[:500],
                        parameters=t.input_schema or {},
                        source=self.name,
                    )
                    for t in tools
                ]
                self.status, self.error = "connected", None
                self._ready.set()
                while True:
                    item = await self._calls.get()
                    if item is None:
                        break
                    name, arguments, future = item
                    try:
                        result = await client.call_tool(name, arguments)
                        if not future.done():
                            future.set_result(result)
                    except Exception as exc:  # noqa: BLE001 - passed to the caller
                        if not future.done():
                            future.set_exception(exc)
            self.status = "stopped"
        except asyncio.CancelledError:
            self.status = "stopped"
            raise
        except BaseException as exc:  # noqa: BLE001 - includes anyio exception groups
            self.status = "failed"
            self.error = _describe(exc)
            log.warning("MCP server %s failed: %s", self.name, self.error)
        finally:
            self._ready.set()
            while not self._calls.empty():
                item = self._calls.get_nowait()
                if item is not None and not item[2].done():
                    item[2].set_exception(ToolError(f"The MCP server {self.name} isn't connected."))

    async def ready(self, timeout: float = CONNECT_TIMEOUT) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout)

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        await self.ready()
        if self.status != "connected":
            raise ToolError(
                f"The MCP server {self.name} isn't connected: {self.error or self.status}."
            )
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._calls.put((tool, arguments, future))
        try:
            return await asyncio.wait_for(future, CALL_TIMEOUT + 5)
        except TimeoutError as exc:
            raise ToolError(f"{self.name}.{tool} took too long to answer.") from exc

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._calls.put(None)
        try:
            await asyncio.wait_for(asyncio.shield(self._task), 5)
        except (TimeoutError, Exception):  # noqa: BLE001
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
        self.status = "stopped"


def _describe(exc: BaseException) -> str:
    leaves: list[BaseException] = []

    def walk(error: BaseException) -> None:
        nested = getattr(error, "exceptions", None)
        if nested:
            for inner in nested:
                walk(inner)
        else:
            leaves.append(error)

    walk(exc)
    error = leaves[0] if leaves else exc
    if isinstance(error, FileNotFoundError):
        return f"the command couldn't be found ({error.filename or error})"
    text = str(error) or type(error).__name__
    return text[:300]


class McpHub:
    """The configured MCP servers and their connections."""

    def __init__(self, path: Path | None = None, *, connect: bool = True) -> None:
        self.path = path
        self.configs = load_configs(path)
        self.connections: dict[str, McpConnection] = {}
        self._connect = connect
        self._started = False

    def start(self) -> None:
        """Connect to every configured server (in the background)."""
        if self._started or not self._connect:
            return
        self._started = True
        for name, config in self.configs.items():
            self._open(name, config)

    def _open(self, name: str, config: McpServerConfig, target: Any = None) -> McpConnection:
        connection = McpConnection(name, config)
        self.connections[name] = connection
        connection.start(target)
        return connection

    async def put(self, name: str, config: McpServerConfig, target: Any = None) -> McpConnection:
        """Add or change a server, save the settings and (re)connect."""
        check_name(name)
        await self.remove(name, save=False)
        self.configs[name] = config
        save_configs(self.path, self.configs)
        connection = self._open(name, config, target)
        await connection.ready()
        return connection

    async def reconnect(self, name: str) -> McpConnection:
        if name not in self.configs:
            raise KeyError(name)
        old = self.connections.pop(name, None)
        if old is not None:
            await old.stop()
        connection = self._open(name, self.configs[name])
        await connection.ready()
        return connection

    async def remove(self, name: str, *, save: bool = True) -> bool:
        connection = self.connections.pop(name, None)
        if connection is not None:
            await connection.stop()
        existed = self.configs.pop(name, None) is not None
        if save and existed:
            save_configs(self.path, self.configs)
        return existed

    def specs(self) -> dict[str, ToolSpec]:
        return {
            spec.id: spec
            for connection in self.connections.values()
            if connection.status == "connected"
            for spec in connection.tools
        }

    async def call(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        _, server, name = tool.split(":", 2)
        connection = self.connections.get(server)
        if connection is None:
            raise ToolError(f"There is no MCP server called {server}.")
        try:
            result = await connection.call(name, arguments)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - protocol errors reach the agent as text
            raise ToolError(f"{server}.{name} failed: {_describe(exc)}") from exc
        text = result_text(result)
        failed = bool(getattr(result, "is_error", False))
        shown = ", ".join(
            f"{key}: {' '.join(str(value).split())[:40]}"
            for key, value in list(arguments.items())[:2]
        )
        summary = f"used {server}.{name}" + (f" ({shown})" if shown else "")
        summary += " (it reported an error)" if failed else ""
        return ToolResult(not failed, text, summary)

    async def shutdown(self) -> None:
        for connection in list(self.connections.values()):
            await connection.stop()
        self.connections.clear()
