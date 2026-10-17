"""A fake model provider for engine tests: scripted answers, recorded requests."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable

from moot.providers import ChatRequest, ChatResult, ModelInfo, StreamEnd, TextDelta, Usage

Script = Callable[[ChatRequest], "list[str] | Exception"]


def speaker_of(request: ChatRequest) -> str:
    """The agent a request is for, read from 'You are <Name>' in the system prompt."""
    match = re.match(r"You are ([^\s,(]+)", request.messages[0].content)
    return match.group(1) if match else "?"


def round_of(request: ChatRequest) -> int:
    match = re.search(r"^Round (\d+) of \d+", request.messages[-1].content, re.MULTILINE)
    return int(match.group(1)) if match else 0


def default_script(request: ChatRequest) -> list[str]:
    return [f"{speaker_of(request)} ", f"round {round_of(request)}"]


class FakeProvider:
    name = "Fake"

    def __init__(self, script: Script | None = None) -> None:
        self.script = script or default_script
        self.requests: list[ChatRequest] = []
        self.closed = False

    async def stream(self, request: ChatRequest) -> AsyncIterator[TextDelta | StreamEnd]:
        self.requests.append(request)
        result = self.script(request)
        if isinstance(result, Exception):
            raise result
        for chunk in result:
            yield TextDelta(chunk)
        yield StreamEnd(finish_reason="stop", usage=Usage(prompt_tokens=10, completion_tokens=5))

    async def complete(self, request: ChatRequest) -> ChatResult:
        parts = [e.text async for e in self.stream(request) if isinstance(e, TextDelta)]
        return ChatResult(text="".join(parts), finish_reason="stop", usage=None)

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def aclose(self) -> None:
        self.closed = True
