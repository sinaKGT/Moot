"""Tests for the provider presets."""

from __future__ import annotations

import asyncio

import pytest

from moot.providers import PRESETS, create_provider
from moot.providers.registry import CLOUD_READ_TIMEOUT, LOCAL_READ_TIMEOUT


def close(provider) -> None:
    asyncio.run(provider.aclose())


def test_core_presets_exist():
    for preset_id in ("openrouter", "ollama", "lmstudio", "custom"):
        assert preset_id in PRESETS


def test_openrouter_reads_key_from_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    provider = create_provider("openrouter")
    try:
        assert provider.config.api_key == "sk-env"
        assert provider.config.base_url == "https://openrouter.ai/api/v1"
        assert provider.config.include_usage is True
        assert provider.config.read_timeout == CLOUD_READ_TIMEOUT
    finally:
        close(provider)


def test_explicit_key_wins_over_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    provider = create_provider("openrouter", api_key="sk-explicit")
    try:
        assert provider.config.api_key == "sk-explicit"
    finally:
        close(provider)


def test_local_presets_need_no_key_and_wait_longer():
    for preset_id, url in (
        ("ollama", "http://localhost:11434/v1"),
        ("lmstudio", "http://localhost:1234/v1"),
    ):
        provider = create_provider(preset_id)
        try:
            assert provider.config.api_key is None
            assert provider.config.base_url == url
            assert provider.config.local is True
            assert provider.config.read_timeout == LOCAL_READ_TIMEOUT
        finally:
            close(provider)


def test_custom_needs_a_base_url():
    with pytest.raises(ValueError, match="base URL"):
        create_provider("custom")
    provider = create_provider("custom", base_url="http://localhost:8080/v1")
    try:
        assert provider.config.base_url == "http://localhost:8080/v1"
    finally:
        close(provider)


def test_unknown_provider_lists_choices():
    with pytest.raises(ValueError, match="ollama"):
        create_provider("nope")
