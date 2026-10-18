"""Tools for agents: built-ins (web search, reading a page, a calculator) and MCP servers."""

from moot.tools.base import ToolError, ToolResult, ToolSpec
from moot.tools.builtins import BUILT_INS, SEARCH_HELP, SEARCH_LABELS, search_service
from moot.tools.mcp import MCP_FILE, McpHub, McpServerConfig
from moot.tools.toolbox import ToolBox

__all__ = [
    "BUILT_INS",
    "MCP_FILE",
    "SEARCH_HELP",
    "SEARCH_LABELS",
    "McpHub",
    "McpServerConfig",
    "ToolBox",
    "ToolError",
    "ToolResult",
    "ToolSpec",
    "search_service",
]
