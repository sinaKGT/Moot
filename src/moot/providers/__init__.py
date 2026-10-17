"""Connections to local (Ollama, LM Studio, OpenAI-compatible) and cloud model providers."""

from moot.providers.base import (
    ChatRequest,
    ChatResult,
    Message,
    ModelInfo,
    ModelNotFoundError,
    Provider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    StreamEnd,
    StreamEvent,
    TextDelta,
    Usage,
)
from moot.providers.openai_compat import OpenAICompatibleProvider, ProviderConfig
from moot.providers.registry import PRESETS, ProviderPreset, create_provider
from moot.providers.structured import (
    StructuredOutputError,
    complete_structured,
    extract_json_object,
)

__all__ = [
    "PRESETS",
    "ChatRequest",
    "ChatResult",
    "Message",
    "ModelInfo",
    "ModelNotFoundError",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderAuthError",
    "ProviderConfig",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderPreset",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "StreamEnd",
    "StreamEvent",
    "StructuredOutputError",
    "TextDelta",
    "Usage",
    "complete_structured",
    "create_provider",
    "extract_json_object",
]
