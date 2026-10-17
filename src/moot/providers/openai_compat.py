"""Provider for any server that speaks the OpenAI chat-completions API.

One class covers OpenRouter, OpenAI, Groq, DeepSeek, Gemini's compatible endpoint,
Ollama, LM Studio, llama.cpp server and vLLM.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from moot.providers.base import (
    ChatRequest,
    ChatResult,
    ModelInfo,
    ModelNotFoundError,
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

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str | None = field(default=None, repr=False)
    api_key_env: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    local: bool = False
    include_usage: bool = False
    json_mode: bool = False  # server accepts response_format={"type": "json_object"}
    connect_timeout: float = 10.0
    read_timeout: float = 120.0
    max_retries: int = 2
    retry_backoff: float = 0.5


class OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.name = config.name
        self._base_url = config.base_url.rstrip("/")
        self._json_mode = config.json_mode
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.read_timeout, connect=config.connect_timeout)
        )

    async def __aenter__(self) -> OpenAICompatibleProvider:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ public

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        finish_reason: str | None = None
        usage: Usage | None = None

        async with self._open_chat(request) as response:
            try:
                async for line in response.aiter_lines():
                    data = _sse_data(line)
                    if data is None:
                        continue
                    if data == "[DONE]":
                        break
                    chunk = self._parse_chunk(data)
                    usage = _parse_usage(chunk.get("usage")) or usage
                    for choice in chunk.get("choices") or ():
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if text:
                            yield TextDelta(text)
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
            except httpx.TimeoutException as exc:
                raise ProviderConnectionError(
                    self.name, "the response stalled and timed out"
                ) from exc
            except httpx.TransportError as exc:
                raise ProviderConnectionError(
                    self.name, f"the connection dropped mid-response ({type(exc).__name__})"
                ) from exc

        yield StreamEnd(finish_reason=finish_reason, usage=usage)

    async def complete(self, request: ChatRequest) -> ChatResult:
        parts: list[str] = []
        end: StreamEnd | None = None
        async for event in self.stream(request):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            else:
                end = event
        return ChatResult(
            text="".join(parts),
            finish_reason=end.finish_reason if end else None,
            usage=end.usage if end else None,
        )

    async def list_models(self) -> list[ModelInfo]:
        async with self._send("GET", "/models") as response:
            body = await response.aread()
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise ProviderResponseError(self.name, "the model list was not valid JSON") from exc

        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ProviderResponseError(self.name, "the model list had an unexpected format")

        models: list[ModelInfo] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            name = item.get("name")
            context = item.get("context_length")
            models.append(
                ModelInfo(
                    id=item["id"],
                    name=name if isinstance(name, str) else None,
                    context_length=context if isinstance(context, int) else None,
                )
            )
        return sorted(models, key=lambda model: model.id)

    # ----------------------------------------------------------------- helpers

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.config.headers}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if stream and self.config.include_usage:
            payload["stream_options"] = {"include_usage": True}
        if request.json_mode and self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @asynccontextmanager
    async def _open_chat(self, request: ChatRequest) -> AsyncIterator[httpx.Response]:
        """Open a streamed chat response. If the server rejects JSON mode, retry without it
        and remember not to ask again."""
        payload = self._payload(request, stream=True)
        opened = False
        try:
            async with self._send("POST", "/chat/completions", json_body=payload) as response:
                opened = True
                yield response
                return
        except ProviderResponseError as exc:
            if opened or exc.status != 400 or "response_format" not in payload:
                raise
        self._json_mode = False
        retry_payload = self._payload(request, stream=True)
        async with self._send("POST", "/chat/completions", json_body=retry_payload) as response:
            yield response

    @asynccontextmanager
    async def _send(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> AsyncIterator[httpx.Response]:
        """Send a request with retries for temporary failures; yields an open response."""
        url = f"{self._base_url}{path}"
        attempt = 0
        while True:
            try:
                request = self._client.build_request(
                    method, url, json=json_body, headers=self._headers()
                )
                response = await self._client.send(request, stream=True)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt < self.config.max_retries:
                    attempt += 1
                    await self._wait_before_retry(attempt)
                    continue
                raise self._connection_error() from exc
            except httpx.TimeoutException as exc:
                raise ProviderConnectionError(self.name, f"timed out waiting for {url}") from exc
            except httpx.TransportError as exc:
                raise self._connection_error() from exc

            if response.status_code in RETRYABLE_STATUS and attempt < self.config.max_retries:
                retry_after = _retry_after_seconds(response)
                await response.aclose()
                attempt += 1
                await self._wait_before_retry(attempt, retry_after)
                continue

            if response.status_code >= 400:
                body = await response.aread()
                await response.aclose()
                raise self._http_error(response.status_code, body)
            break

        try:
            yield response
        finally:
            await response.aclose()

    async def _wait_before_retry(self, attempt: int, retry_after: float | None = None) -> None:
        delay = (
            retry_after
            if retry_after is not None
            else self.config.retry_backoff * 2 ** (attempt - 1)
        )
        await asyncio.sleep(delay)

    def _connection_error(self) -> ProviderConnectionError:
        hint = "Is it running?" if self.config.local else "Check your internet connection."
        return ProviderConnectionError(self.name, f"can't reach {self._base_url}. {hint}")

    def _http_error(self, status: int, body: bytes) -> ProviderError:
        detail = _error_detail(body)
        if status in (401, 403):
            if not self.config.api_key:
                message = "no API key was provided."
            else:
                message = f"the API key was rejected (HTTP {status})."
            if self.config.api_key_env:
                message += f" Set {self.config.api_key_env}."
            return ProviderAuthError(self.name, f"{message} {detail}".strip(), status=status)
        if status == 404:
            return ModelNotFoundError(
                self.name, f"not found (HTTP 404). {detail}".strip(), status=status
            )
        if status == 429:
            return ProviderRateLimitError(
                self.name, f"too many requests (HTTP 429). {detail}".strip(), status=status
            )
        return ProviderResponseError(self.name, f"HTTP {status}. {detail}".strip(), status=status)

    def _parse_chunk(self, data: str) -> dict[str, Any]:
        try:
            chunk = json.loads(data)
        except ValueError as exc:
            raise ProviderResponseError(self.name, "received a malformed stream chunk") from exc
        if not isinstance(chunk, dict):
            raise ProviderResponseError(self.name, "received an unexpected stream chunk")
        if "error" in chunk:
            raise ProviderResponseError(self.name, _error_detail(json.dumps(chunk).encode()))
        return chunk


def _sse_data(line: str) -> str | None:
    """Return the payload of a server-sent-events `data:` line, or None to skip the line."""
    line = line.strip()
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    return line[len("data:") :].strip()


def _parse_usage(raw: object) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    if isinstance(prompt, int) and isinstance(completion, int):
        return Usage(prompt_tokens=prompt, completion_tokens=completion)
    return None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if 0 <= seconds <= MAX_RETRY_AFTER_SECONDS else None


def _error_detail(body: bytes) -> str:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body.decode("utf-8", "replace").strip()[:300]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message", "")).strip()[:300]
    if isinstance(error, str):
        return error.strip()[:300]
    return str(data)[:300]
