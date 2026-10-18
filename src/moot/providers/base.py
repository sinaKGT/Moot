"""Shared types for talking to language models.

Every provider (OpenRouter, Ollama, LM Studio, ...) speaks in these types, so the
rest of Moot never depends on a specific vendor's API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: Sequence[Message]
    temperature: float | None = None
    max_tokens: int | None = None
    json_mode: bool = False  # ask the server for a JSON object, where it supports that

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        if not self.messages:
            raise ValueError("ChatRequest needs at least one message")


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A piece of generated text, delivered as soon as the model produces it."""

    text: str


@dataclass(frozen=True, slots=True)
class StreamEnd:
    """The last event of every stream."""

    finish_reason: str | None
    usage: Usage | None


StreamEvent = TextDelta | StreamEnd


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    finish_reason: str | None
    usage: Usage | None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    name: str | None = None
    context_length: int | None = None


@dataclass(frozen=True, slots=True)
class ContextWindow:
    """How many tokens a model can read and write at once, as its server reports it."""

    tokens: int
    loaded: bool = True  # False: not loaded yet; `tokens` is what it will most likely get
    max_tokens: int | None = None  # the most the model itself supports, when known


class ProviderError(Exception):
    """Base class for every provider failure."""

    def __init__(self, provider: str, message: str, *, status: int | None = None) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message
        self.status = status  # HTTP status code, when the error came from one


class ProviderConnectionError(ProviderError):
    """The provider could not be reached, or the connection dropped."""


class ProviderAuthError(ProviderError):
    """The API key is missing or was rejected."""


class ProviderRateLimitError(ProviderError):
    """The provider is limiting requests; try again later."""


class ModelNotFoundError(ProviderError):
    """The requested model (or endpoint) does not exist on the provider."""


class ProviderResponseError(ProviderError):
    """The provider returned an error or a response Moot could not read."""


@runtime_checkable
class Embedder(Protocol):
    """A provider that can also turn texts into vectors (for searching agents' files)."""

    name: str

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class Provider(Protocol):
    """What the engine needs from any model provider."""

    name: str

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]: ...

    async def complete(self, request: ChatRequest) -> ChatResult: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def aclose(self) -> None: ...
