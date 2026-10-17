"""A fake model provider for engine tests: scripted answers, recorded requests."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable

from moot.engine.moderator import (
    OPENING_TASK,
    REPORT_TASK,
    SELECTION_TASK,
    SUMMARY_TASK,
    TOPIC_TASK,
)
from moot.memory import GRAPH_BUILDER_INTRO
from moot.providers import ChatRequest, ChatResult, ModelInfo, StreamEnd, TextDelta, Usage

Script = Callable[[ChatRequest], "list[str] | Exception"]


def speaker_of(request: ChatRequest) -> str:
    """The agent a request is for, read from 'You are <Name>' in the system prompt."""
    match = re.match(r"You are ([^\s,(]+)", request.messages[0].content)
    return match.group(1) if match else "?"


def round_of(request: ChatRequest) -> int:
    match = re.search(r"^Round (\d+) of \d+", request.messages[-1].content, re.MULTILINE)
    return int(match.group(1)) if match else 0


def is_graph_request(request: ChatRequest) -> bool:
    return request.messages[0].content.startswith(GRAPH_BUILDER_INTRO)


def graph_speaker(request: ChatRequest) -> str:
    match = re.search(r"New message from ([^\s(:]+)", request.messages[-1].content)
    return match.group(1) if match else "?"


def moderator_reply(request: ChatRequest) -> list[str] | None:
    """Canned answers to the moderator's prompts, or None if this isn't one."""
    user = request.messages[-1].content
    if TOPIC_TASK in user:
        return ['{"clear": true}']
    if "the moderator of a discussion" not in request.messages[0].content:
        return None
    if OPENING_TASK in user:
        plan = {"subquestions": ["Is it healthy?", "Is it practical?"]}
        return ["Welcome. We will ask:\n1. Is it healthy?\n", f"<plan>{json.dumps(plan)}</plan>"]
    if SELECTION_TASK in user:
        match = re.search(r"Who still has turns in this round:\n- ([^:]+):", user)
        return [json.dumps({"next": match.group(1) if match else "?", "reason": "test choice"})]
    if SUMMARY_TASK in user:
        return ["Summary of the round."]
    if REPORT_TASK in user:
        return ["Answer: undecided."]
    return None


def default_script(request: ChatRequest) -> list[str]:
    reply = moderator_reply(request)
    if reply is not None:
        return reply
    if is_graph_request(request):
        point = {"ref": "N1", "type": "claim", "text": f"{graph_speaker(request)} main point"}
        return [json.dumps({"nodes": [point], "links": []})]
    return [f"{speaker_of(request)} ", f"round {round_of(request)}"]


class FakeProvider:
    name = "Fake"

    def __init__(self, script: Script | None = None, *, graph_delay: float = 0.0) -> None:
        self.script = script or default_script
        self.graph_delay = graph_delay
        self.requests: list[ChatRequest] = []
        self.closed = False

    async def stream(self, request: ChatRequest) -> AsyncIterator[TextDelta | StreamEnd]:
        self.requests.append(request)
        if self.graph_delay and is_graph_request(request):
            await asyncio.sleep(self.graph_delay)
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
