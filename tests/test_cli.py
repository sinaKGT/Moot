"""Tests for the `moot` command-line tool."""

from __future__ import annotations

import httpx

from moot import cli
from moot.cli import main


def test_providers_command_lists_presets(capsys):
    assert main(["providers"]) == 0
    out = capsys.readouterr().out
    assert "openrouter" in out
    assert "OPENROUTER_API_KEY" in out
    assert "ollama" in out


def test_ask_streams_the_answer(monkeypatch, capsys, make_provider, sse, chunk):
    body = sse(
        chunk("Hello "),
        chunk("there", finish="stop"),
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
    )
    provider = make_provider(lambda request: httpx.Response(200, content=body))
    monkeypatch.setattr(cli, "create_provider", lambda *args, **kwargs: provider)

    assert main(["ask", "ollama", "some-model", "Hi"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "Hello there\n"
    assert "3 in, 2 out" in captured.err


def test_ask_reports_provider_errors(monkeypatch, capsys, make_provider):
    provider = make_provider(
        lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}),
        api_key_env="OPENROUTER_API_KEY",
    )
    monkeypatch.setattr(cli, "create_provider", lambda *args, **kwargs: provider)

    assert main(["ask", "openrouter", "m", "Hi"]) == 1
    assert "Error:" in capsys.readouterr().err


def test_models_command(monkeypatch, capsys, make_provider):
    payload = {"data": [{"id": "llama3.2"}, {"id": "qwen3", "context_length": 32768}]}
    provider = make_provider(lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr(cli, "create_provider", lambda *args, **kwargs: provider)

    assert main(["models", "ollama"]) == 0
    assert capsys.readouterr().out == "llama3.2\nqwen3  (32,768 tokens)\n"


def test_unknown_provider_exits_with_usage_error(capsys):
    assert main(["models", "nope"]) == 2
    assert "Unknown provider" in capsys.readouterr().err
