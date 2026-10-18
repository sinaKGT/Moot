"""Before an agent with tools speaks, it may use them: up to a few steps, each one a short
structured call ("use this tool with these arguments" or "ready"), then the tool runs. What it
found goes into its turn. Works with any model, since it needs JSON, not native tool calling.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, field_validator

from moot.providers import trimmable
from moot.tools import ToolResult, ToolSpec

MAX_TOOL_STEPS = 2
STEP_RESULT_CHARS = 1200
FINDING_CHARS = 1500
FINDINGS_CHARS = 5000


class ToolChoice(BaseModel):
    model_config = {"extra": "ignore"}

    tool: str | None = None
    arguments: dict[str, Any] = {}

    @field_validator("tool", mode="before")
    @classmethod
    def _tool(cls, value: object) -> str | None:
        text = str(value).strip() if value is not None else ""
        return None if text.lower() in ("", "none", "null", "ready") else text

    @field_validator("arguments", mode="before")
    @classmethod
    def _arguments(cls, value: object) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return {}
        return value if isinstance(value, dict) else {}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def describe_arguments(schema: dict[str, Any]) -> str:
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    parts = []
    for name, info in list(properties.items())[:12]:
        kind = info.get("type", "any") if isinstance(info, dict) else "any"
        note = ", required" if name in required else ""
        parts.append(f"{name} ({kind}{note})")
    return ", ".join(parts) or "none"


def tool_prompt(specs: list[ToolSpec], found: list[ToolResult], steps_left: int) -> str:
    lines = [
        "",
        "Before you speak, you may use your tools to check a fact or find evidence for your "
        "point. Everyone will see what you looked up. Your tools:",
    ]
    lines += [
        f"- {spec.name}: {spec.description} Arguments: {describe_arguments(spec.parameters)}"
        for spec in specs
    ]
    if found:
        lines += ["", "What you found so far:"]
        results = [
            f"{i}. {result.summary}\n{_clip(result.text, STEP_RESULT_CHARS)}"
            for i, result in enumerate(found, 1)
        ]
        lines.append(trimmable(results, drop="last", priority=2, keep=1))
    lines += [
        "",
        'Reply with only a JSON object: {"tool": "<name>", "arguments": {...}} to use a tool '
        f'(at most {steps_left} more), or {{"tool": null}} when you are ready to speak. Use a '
        "tool only when it helps this turn.",
    ]
    return "\n".join(lines)


def find_spec(specs: list[ToolSpec], name: str) -> ToolSpec | None:
    wanted = name.strip().lower()
    for spec in specs:
        if wanted in (spec.name.lower(), spec.id.lower()):
            return spec
    short = [s for s in specs if s.name.lower().split(".")[-1] == wanted]
    return short[0] if len(short) == 1 else None


def findings_block(found: list[ToolResult]) -> str:
    """What the agent found, for its turn."""
    useful = [r for r in found if r.ok]
    if not useful:
        return ""
    lines = [
        "What you found with your tools (everyone saw that you used them; if you rely on a "
        "result, say where it comes from, e.g. the site's name or link):"
    ]
    pieces: list[str] = []
    used = 0
    for i, result in enumerate(useful, 1):
        piece = f"{i}. {result.summary}\n{_clip(result.text, FINDING_CHARS)}"
        if used + len(piece) > FINDINGS_CHARS:
            break
        pieces.append(piece)
        used += len(piece)
    lines.append(trimmable(pieces, drop="last", priority=2, keep=1))
    return "\n".join(lines)
