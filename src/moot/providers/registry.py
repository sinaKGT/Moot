"""Ready-made settings for the model providers Moot knows about.

API keys are read from environment variables for now; saved settings come with the
server step.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from moot.providers.openai_compat import OpenAICompatibleProvider, ProviderConfig

CLOUD_READ_TIMEOUT = 120.0
LOCAL_READ_TIMEOUT = 600.0  # local servers may need time to load a model


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    id: str
    label: str
    base_url: str | None
    api_key_env: str | None
    local: bool
    include_usage: bool = False
    headers: Mapping[str, str] = field(default_factory=dict)


PRESETS: dict[str, ProviderPreset] = {
    preset.id: preset
    for preset in (
        ProviderPreset(
            "openrouter",
            "OpenRouter",
            "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY",
            local=False,
            include_usage=True,
            headers={"X-Title": "Moot"},
        ),
        ProviderPreset(
            "openai",
            "OpenAI",
            "https://api.openai.com/v1",
            "OPENAI_API_KEY",
            local=False,
            include_usage=True,
        ),
        ProviderPreset(
            "groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", local=False
        ),
        ProviderPreset(
            "deepseek", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", local=False
        ),
        ProviderPreset(
            "gemini",
            "Google Gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "GEMINI_API_KEY",
            local=False,
        ),
        ProviderPreset("ollama", "Ollama", "http://localhost:11434/v1", None, local=True),
        ProviderPreset("lmstudio", "LM Studio", "http://localhost:1234/v1", None, local=True),
        ProviderPreset(
            "custom", "Custom (OpenAI-compatible)", None, "MOOT_CUSTOM_API_KEY", local=True
        ),
    )
}


def create_provider(
    preset_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> OpenAICompatibleProvider:
    """Create a provider from a preset, optionally overriding its key or address."""
    try:
        preset = PRESETS[preset_id]
    except KeyError:
        choices = ", ".join(PRESETS)
        raise ValueError(f"Unknown provider '{preset_id}'. Choose one of: {choices}.") from None

    url = base_url or preset.base_url
    if not url:
        raise ValueError(f"Provider '{preset_id}' needs a base URL (use --base-url).")

    key = api_key or (os.environ.get(preset.api_key_env) if preset.api_key_env else None) or None

    config = ProviderConfig(
        name=preset.label,
        base_url=url,
        api_key=key,
        api_key_env=preset.api_key_env,
        headers=dict(preset.headers),
        local=preset.local,
        include_usage=preset.include_usage,
        read_timeout=LOCAL_READ_TIMEOUT if preset.local else CLOUD_READ_TIMEOUT,
    )
    return OpenAICompatibleProvider(config, client=client)
