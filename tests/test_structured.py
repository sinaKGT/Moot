"""Tests for structured (JSON) answers and the provider's JSON mode."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fakes import FakeProvider
from pydantic import BaseModel

from moot.providers import (
    ChatRequest,
    Message,
    ProviderResponseError,
    StructuredOutputError,
    complete_structured,
    extract_json_object,
)

REQUEST = ChatRequest(model="m", messages=[Message("user", "Give me JSON")])


class Answer(BaseModel):
    verdict: str
    score: int


@pytest.mark.parametrize(
    "text",
    [
        '{"verdict": "yes", "score": 3}',
        'Here you go:\n```json\n{"verdict": "yes", "score": 3}\n```',
        '<think>Let me think {about it}.</think>{"verdict": "yes", "score": 3}',
        'reasoning without a start tag</think> {"verdict": "yes", "score": 3}',
        'Not this {broken, but this: {"verdict": "yes", "score": 3}',
    ],
)
def test_extract_json_object(text):
    assert extract_json_object(text) == {"verdict": "yes", "score": 3}


def test_extract_keeps_nested_objects():
    assert extract_json_object('x {"a": {"b": [1, {"c": 2}]}} y') == {"a": {"b": [1, {"c": 2}]}}


def test_extract_fails_without_json():
    with pytest.raises(ValueError):
        extract_json_object("no braces here")


def test_complete_structured_retries_with_the_problem():
    answers = iter(["not json at all", '{"verdict": "ok", "score": 1}'])
    provider = FakeProvider(lambda request: [next(answers)])

    result = asyncio.run(complete_structured(provider, REQUEST, Answer))

    assert result == Answer(verdict="ok", score=1)
    assert all(r.json_mode for r in provider.requests)
    retry = provider.requests[1].messages
    assert [m.role for m in retry] == ["user", "assistant", "user"]
    assert "could not be used" in retry[-1].content


def test_complete_structured_gives_up():
    provider = FakeProvider(lambda request: ['{"verdict": "missing score"}'])
    with pytest.raises(StructuredOutputError, match="2 attempts"):
        asyncio.run(complete_structured(provider, REQUEST, Answer, retries=1))
    assert len(provider.requests) == 2


def _sse(text: str) -> bytes:
    chunk = {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def test_json_mode_is_sent_when_the_server_supports_it(make_provider):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, content=_sse("{}"))

    json_request = ChatRequest(model="m", messages=[Message("user", "x")], json_mode=True)
    asyncio.run(make_provider(handler, json_mode=True).complete(json_request))
    asyncio.run(make_provider(handler, json_mode=False).complete(json_request))
    asyncio.run(make_provider(handler, json_mode=True).complete(REQUEST))

    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in payloads[1]
    assert "response_format" not in payloads[2]


def test_json_mode_rejected_by_the_server_falls_back_and_is_remembered(make_provider):
    payloads = []

    def handler(request):
        body = json.loads(request.content)
        payloads.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format not allowed"}})
        return httpx.Response(200, content=_sse('{"ok": true}'))

    provider = make_provider(handler, json_mode=True)
    json_request = ChatRequest(model="m", messages=[Message("user", "x")], json_mode=True)

    assert asyncio.run(provider.complete(json_request)).text == '{"ok": true}'
    assert asyncio.run(provider.complete(json_request)).text == '{"ok": true}'
    assert ["response_format" in p for p in payloads] == [True, False, False]


def test_other_400_errors_are_not_retried(make_provider):
    provider = make_provider(
        lambda request: httpx.Response(400, json={"error": {"message": "bad request"}})
    )
    with pytest.raises(ProviderResponseError) as caught:
        asyncio.run(provider.complete(REQUEST))
    assert caught.value.status == 400
