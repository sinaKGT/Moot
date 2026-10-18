"""All the tools Moot can give an agent: the built-ins and those of connected MCP servers."""

from __future__ import annotations

import json
from typing import Any

import httpx

from moot.tools.base import ToolError, ToolResult, ToolSpec
from moot.tools.builtins import (
    BUILT_INS,
    SEARCH_HELP,
    SEARCH_LABELS,
    USER_AGENT,
    calculator,
    fetch_page,
    search_service,
    web_search,
)
from moot.tools.mcp import McpHub


class ToolBox:
    def __init__(
        self, mcp: McpHub | None = None, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self.mcp = mcp or McpHub()
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20.0, headers={"User-Agent": USER_AGENT})
        return self._client

    def available(self, tool_id: str) -> tuple[bool, str]:
        """Whether a tool can be used now, and why not."""
        if tool_id == "web_search":
            service = search_service()
            return (True, SEARCH_LABELS[service]) if service else (False, SEARCH_HELP)
        if tool_id in BUILT_INS:
            return True, ""
        if tool_id.startswith("mcp:"):
            server = tool_id.split(":", 2)[1]
            connection = self.mcp.connections.get(server)
            if connection is None:
                return False, f"The MCP server {server} isn't set up."
            if connection.status != "connected":
                return False, f"The MCP server {server} isn't connected."
            return (
                tool_id in self.mcp.specs(),
                "" if tool_id in self.mcp.specs() else "That tool is gone.",
            )
        return False, "Unknown tool."

    def specs_for(self, tool_ids: list[str]) -> list[ToolSpec]:
        """The specs of the agent's tools that can be used now."""
        found = {**BUILT_INS, **self.mcp.specs()}
        return [found[t] for t in tool_ids if t in found and self.available(t)[0]]

    async def run(self, spec: ToolSpec, arguments: dict[str, Any]) -> ToolResult:
        """Run a tool. Failures come back as a result the agent can read, never raised."""
        try:
            if spec.id == "web_search":
                return await web_search(self.client, str(arguments.get("query", "")))
            if spec.id == "fetch_page":
                return await fetch_page(self.client, str(arguments.get("url", "")))
            if spec.id == "calculator":
                return calculator(str(arguments.get("expression", "")))
            if spec.id.startswith("mcp:"):
                return await self.mcp.call(spec.id, arguments)
            raise ToolError(f"Unknown tool {spec.name}.")
        except ToolError as exc:
            shown = json.dumps(arguments, ensure_ascii=False)[:120]
            return ToolResult(
                False, f"That didn't work: {exc}", f"{spec.name} {shown} failed: {exc}"
            )

    async def aclose(self) -> None:
        await self.mcp.shutdown()
        if self._client is not None and self._owns_client:
            await self._client.aclose()
