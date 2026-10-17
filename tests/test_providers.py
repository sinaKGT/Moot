"""Tests for the OpenAI-compatible provider, using simulated servers."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from moot.providers import (
    ChatRequest,
    Message,
    ModelInfo,
    ModelNotFoundError,
    ProviderAuthError,
    ProviderConfig,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderResponseError,
    StreamEnd,
    TextDelta,
    Usage,
)

REQUEST = ChatRequest(model="test-model", messages=[Message("user", "Hi")])


def run(coro):
    return asyncio.run(coro)


async def collect(provider, request=REQUEST):
    return [event async for event in provider.stream(request)]


def test_stream_yields_text_then_end(make_provider, sse, chunk):
    body = sse(
        chunk("Hel"),
        chunk("lo"),
        chunk(finish="stop"),
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    )
    provider = make_provider(lambda request: httpx.Response(200, content=body))

    events = run(collect(provider))

    assert events == [
        TextDelta("Hel"),
        TextDelta("lo"),
        StreamEnd(finish_reason="stop", usage=Usage(5, 2)),
    ]


def test_request_payload_and_headers(make_provider, sse, chunk):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["title"] = request.headers.get("x-title")
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, content=sse(chunk("ok", finish="stop")))

    provider = make_provider(
        handler, api_key="sk-test", include_usage=True, headers={"X-Title": "Moot"}
    )
    request = ChatRequest(
        model="m",
        messages=[Message("system", "Be brief"), Message("user", "Hi")],
        temperature=0.3,
        max_tokens=50,
    )
    run(provider.complete(request))

    assert seen["url"] == "http://test.local/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["title"] == "Moot"
    assert seen["json"] == {
        "model": "m",
        "messages": [
            {"role": "system", "content": "Be brief"},
            {"role": "user", "content": "Hi"},
        ],
        "stream": True,
        "temperature": 0.3,
        "max_tokens": 50,
        "stream_options": {"include_usage": True},
    }


def test_optional_fields_are_left_out(make_provider, sse, chunk):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, content=sse(chunk("ok", finish="stop")))

    run(make_provider(handler).complete(REQUEST))

    assert seen["auth"] is None
    assert set(seen["json"]) == {"model", "messages", "stream"}


def test_complete_joins_the_text(make_provider, sse, chunk):
    body = sse(chunk("One, "), chunk("two."), chunk(finish="stop"))
    provider = make_provider(lambda request: httpx.Response(200, content=body))

    result = run(provider.complete(REQUEST))

    assert result.text == "One, two."
    assert result.finish_reason == "stop"
    assert result.usage is None


def test_missing_key_error_names_the_env_var(make_provider):
    provider = make_provider(
        lambda request: httpx.Response(401, json={"error": {"message": "No auth credentials"}}),
        api_key_env="OPENROUTER_API_KEY",
    )
    with pytest.raises(ProviderAuthError, match="no API key was provided.*OPENROUTER_API_KEY"):
        run(provider.complete(REQUEST))


def test_rejected_key(make_provider):
    provider = make_provider(lambda request: httpx.Response(403, text="Forbidden"), api_key="bad")
    with pytest.raises(ProviderAuthError, match="rejected"):
        run(provider.complete(REQUEST))


def test_unknown_model(make_provider):
    provider = make_provider(
        lambda request: httpx.Response(404, json={"error": {"message": "model 'x' not found"}})
    )
    with pytest.raises(ModelNotFoundError, match="model 'x' not found"):
        run(provider.complete(REQUEST))


def test_rate_limit_is_retried_then_succeeds(make_provider, sse, chunk):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, content=sse(chunk("finally", finish="stop")))

    result = run(make_provider(handler).complete(REQUEST))

    assert result.text == "finally"
    assert len(calls) == 3


def test_rate_limit_gives_up_after_retries(make_provider):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    with pytest.raises(ProviderRateLimitError, match="slow down"):
        run(make_provider(handler).complete(REQUEST))
    assert len(calls) == 3


def test_server_error_is_retried(make_provider, sse, chunk):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, content=sse(chunk("ok", finish="stop")))

    assert run(make_provider(handler).complete(REQUEST)).text == "ok"
    assert len(calls) == 2


def test_unreachable_local_server_suggests_starting_it(make_provider):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderConnectionError, match="Is it running"):
        run(make_provider(handler, local=True).complete(REQUEST))
    assert len(calls) == 3


def test_error_inside_the_stream(make_provider, sse):
    body = sse({"error": {"message": "Provider overloaded"}})
    provider = make_provider(lambda request: httpx.Response(200, content=body))
    with pytest.raises(ProviderResponseError, match="overloaded"):
        run(provider.complete(REQUEST))


def test_malformed_stream_chunk(make_provider):
    provider = make_provider(lambda request: httpx.Response(200, content=b"data: {oops\n\n"))
    with pytest.raises(ProviderResponseError, match="malformed"):
        run(provider.complete(REQUEST))


def test_list_models(make_provider):
    payload = {
        "data": [
            {"id": "b-model", "name": "Model B", "context_length": 8192},
            {"id": "a-model"},
            {"name": "no id, skipped"},
        ]
    }
    provider = make_provider(lambda request: httpx.Response(200, json=payload))

    models = run(provider.list_models())

    assert models == [
        ModelInfo(id="a-model"),
        ModelInfo(id="b-model", name="Model B", context_length=8192),
    ]


def test_list_models_rejects_unexpected_format(make_provider):
    provider = make_provider(lambda request: httpx.Response(200, json={"models": []}))
    with pytest.raises(ProviderResponseError, match="unexpected format"):
        run(provider.list_models())


def test_api_key_is_hidden_from_repr():
    config = ProviderConfig(name="X", base_url="http://x", api_key="sk-secret")
    assert "sk-secret" not in repr(config)


def test_request_needs_at_least_one_message():
    with pytest.raises(ValueError):
        ChatRequest(model="m", messages=[])
