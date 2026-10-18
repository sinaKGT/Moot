"""Shared test helpers: simulated model servers for provider tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from moot.providers import OpenAICompatibleProvider, ProviderConfig

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _private_data_dir(tmp_path_factory, monkeypatch):
    """No test ever saves discussions in the real data folder."""
    monkeypatch.setenv("MOOT_DATA_DIR", str(tmp_path_factory.mktemp("moot-data")))


def _chunk(
    text: str | None = None, finish: str | None = None, usage: dict[str, int] | None = None
) -> dict[str, Any]:
    delta = {"content": text} if text is not None else {}
    data: dict[str, Any] = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        data["usage"] = usage
    return data


def _sse(*events: dict[str, Any]) -> bytes:
    """Build a streamed response body, including keep-alive comments like OpenRouter's."""
    lines = [": OPENROUTER PROCESSING", ""]
    for event in events:
        lines += [f"data: {json.dumps(event)}", ""]
    lines += ["data: [DONE]", ""]
    return "\n".join(lines).encode()


@pytest.fixture
def chunk() -> Callable[..., dict[str, Any]]:
    return _chunk


@pytest.fixture
def sse() -> Callable[..., bytes]:
    return _sse


@pytest.fixture
def make_provider() -> Callable[..., OpenAICompatibleProvider]:
    def factory(handler: Handler, **overrides: Any) -> OpenAICompatibleProvider:
        settings: dict[str, Any] = {
            "name": "Test",
            "base_url": "http://test.local/v1",
            "retry_backoff": 0.0,
        }
        settings.update(overrides)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return OpenAICompatibleProvider(ProviderConfig(**settings), client=client)

    return factory
