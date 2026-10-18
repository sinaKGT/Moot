"""What every tool looks like to the engine: a spec the agent reads, and a result it gets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_RESULT_CHARS = 8000


@dataclass(frozen=True)
class ToolSpec:
    id: str  # "web_search", or "mcp:<server>:<tool>"
    name: str  # what the agent writes to use it
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema of the arguments
    source: str = "built-in"  # "built-in" or the MCP server's name


@dataclass
class ToolResult:
    ok: bool
    text: str  # what the agent reads (clipped)
    summary: str  # one line for the transcript, e.g. 'web search: "car-free Oslo"'
    links: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.text) > MAX_RESULT_CHARS:
            self.text = self.text[:MAX_RESULT_CHARS].rsplit(" ", 1)[0] + " …"


class ToolError(Exception):
    """A tool that can't run as asked; the message is shown to the agent and the user."""
