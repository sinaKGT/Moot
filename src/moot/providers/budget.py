"""Keeping requests inside a model's context window.

Local models often run with a small context: LM Studio loads a model with 4,096 tokens
unless told otherwise, and requests running at the same time share that space. Moot's
prompts mark the parts that may be shortened (old messages, earlier points, long lists)
with `trimmable`. `BudgetedProvider` wraps a provider and, for every request:

- finds out how much context the model has (LM Studio reports it; otherwise Moot learns it
  from the server's "context exceeded" errors),
- fits the prompt to it: marked parts lose their oldest (or last) items first and, only if
  that is not enough, the middle of the longest message is cut,
- runs requests to a small-context model one at a time, so they don't crowd each other out,
- and retries a request the server rejected for its length, with a shorter prompt.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, Literal

from moot.providers.base import (
    ChatRequest,
    ChatResult,
    ContextWindow,
    Embedder,
    Message,
    ModelInfo,
    Provider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    StreamEvent,
    TextDelta,
)

log = logging.getLogger(__name__)

CHARS_PER_TOKEN = 3  # cautious: English averages closer to 4 characters per token
SMALL_CONTEXT = 8192  # tokens; below this Moot warns, and one request runs at a time
LMSTUDIO_DEFAULT_CONTEXT = 4096  # what LM Studio gives a model it loads on demand
BETTER_CONTEXT = 16384  # what Moot suggests instead
TEMPLATE_TOKENS = 64  # room for the chat template around the messages
MIN_PROMPT_CHARS = 2000
MAX_CONTEXT_RETRIES = 2
PROBE_SECONDS = 30.0  # how long a context size found out stays trusted
PROBE_TIMEOUT = 5.0
MIN_CUT_CHARS = 200

CONTEXT_ERRORS = re.compile(
    r"context[ _-]?(size|length|window)[^.]{0,40}(exceed|too|limit)"
    r"|(exceed|too long|larger than)[^.]{0,60}context"
    r"|context[ _-]?(length[ _-]?exceeded|overflow)"
    r"|maximum context length|prompt is too long|too many tokens",
    re.IGNORECASE,
)

# ------------------------------------------------------------------ marked regions

_OPEN, _SEP, _CLOSE = "\x1d", "\x1f", "\x1e"  # ASCII group/unit/record separators
_CONTROL = str.maketrans("", "", _OPEN + _SEP + _CLOSE)
_REGION = re.compile("\x1d([ol]):(\\d+):(\\d+):([^\x1f]*)\x1f([^\x1d\x1e]*)\x1e")

Drop = Literal["oldest", "last"]


def trimmable(
    items: Iterable[str],
    *,
    joiner: str = "\n",
    drop: Drop = "oldest",
    priority: int = 0,
    keep: int = 0,
) -> str:
    """The items joined as usual, marked so a small context window may leave some out.

    `drop="oldest"` leaves out the first items first (a transcript, earlier points);
    `drop="last"` the last ones (results ranked best first). Regions with a lower `priority`
    shrink first, and the last `keep` items (first, for "last") stay until every region has
    given up everything else. Providers wrapped by `budgeted` remove the marks.
    """
    cleaned = [render(item) for item in items]
    if not cleaned:
        return ""
    kind = "o" if drop == "oldest" else "l"
    head = f"{_OPEN}{kind}:{max(0, priority)}:{max(0, keep)}:{joiner.translate(_CONTROL)}{_SEP}"
    return head + _SEP.join(cleaned) + _CLOSE


def render(text: str) -> str:
    """The text with every marked region in full and the marks removed."""
    return _REGION.sub(lambda m: m.group(4).join(m.group(5).split(_SEP)), text).translate(_CONTROL)


def render_messages(messages: Sequence[Message]) -> list[Message]:
    return [Message(m.role, render(m.content)) for m in messages]


@dataclass
class _Region:
    drop: str  # "o": oldest first, "l": last first
    priority: int
    keep: int
    joiner: str
    items: list[str]
    left_out: int = 0

    def text(self) -> str:
        n = self.left_out
        if not n:
            return self.joiner.join(self.items)
        if self.drop == "o":
            parts = "part" if n == 1 else "parts"
            note = f"(… {n} earlier {parts} left out to fit the model's context …)"
            return self.joiner.join([note, *self.items[n:]])
        note = f"(… {n} more left out to fit the model's context …)"
        return self.joiner.join([*self.items[: len(self.items) - n], note])

    def can_shrink(self, respect_keep: bool) -> bool:
        remaining = len(self.items) - self.left_out
        return remaining > (self.keep if respect_keep else 0)


def _segments(text: str) -> list[str | _Region]:
    parts: list[str | _Region] = []
    position = 0
    for match in _REGION.finditer(text):
        parts.append(text[position : match.start()].translate(_CONTROL))
        kind, priority, keep, joiner, body = match.groups()
        parts.append(_Region(kind, int(priority), int(keep), joiner, body.split(_SEP)))
        position = match.end()
    parts.append(text[position:].translate(_CONTROL))
    return parts


def _joined(segments: list[str | _Region]) -> str:
    return "".join(s if isinstance(s, str) else s.text() for s in segments)


def fit(messages: Sequence[Message], max_chars: int) -> list[Message]:
    """The messages with the marks removed, shortened where marked (and, if that is not
    enough, cut in the middle) so that together they are at most `max_chars` long."""
    parsed = [_segments(m.content) for m in messages]
    regions = [s for segments in parsed for s in segments if isinstance(s, _Region)]
    total = sum(len(_joined(segments)) for segments in parsed)
    for respect_keep in (True, False):
        while total > max_chars:
            open_regions = [r for r in regions if r.can_shrink(respect_keep)]
            if not open_regions:
                break
            lowest = min(r.priority for r in open_regions)
            region = max(
                (r for r in open_regions if r.priority == lowest), key=lambda r: len(r.text())
            )
            before = len(region.text())
            region.left_out += 1
            total += len(region.text()) - before
    texts = [_joined(segments) for segments in parsed]
    if total > max_chars:
        texts = _cut(texts, max_chars)
    return [Message(m.role, text) for m, text in zip(messages, texts, strict=True)]


_CUT_NOTE = "\n(… part of this was left out to fit the model's context …)\n"


def _cut(texts: list[str], max_chars: int) -> list[str]:
    """Last resort: cut the middle out of the longest texts, keeping their start (who is
    speaking, the task) and their end (the instructions for the answer)."""
    texts = list(texts)
    excess = sum(map(len, texts)) - max_chars
    for i in sorted(range(len(texts)), key=lambda i: len(texts[i]), reverse=True):
        if excess <= 0:
            break
        text = texts[i]
        keep = max(MIN_CUT_CHARS, len(text) - excess - len(_CUT_NOTE))
        if keep >= len(text):
            continue
        head = keep * 2 // 5
        texts[i] = text[:head] + _CUT_NOTE + text[len(text) - (keep - head) :]
        excess -= len(text) - len(texts[i])
    return texts


# ------------------------------------------------------------------ context sizes


def prompt_chars(tokens: int) -> int:
    """How many characters of prompt fit a context of `tokens`, leaving room for the answer
    (and for a reasoning model's thinking)."""
    answer = min(4096, max(768, tokens // 3))
    return max(MIN_PROMPT_CHARS, (tokens - answer - TEMPLATE_TOKENS) * CHARS_PER_TOKEN)


def is_context_error(error: Exception) -> bool:
    """Whether the server refused a request because it didn't fit the context window."""
    if not isinstance(error, ProviderError) or isinstance(
        error, ProviderAuthError | ProviderConnectionError | ProviderRateLimitError
    ):
        return False
    return bool(CONTEXT_ERRORS.search(str(error)))


def lmstudio_window(data: object, model: str) -> ContextWindow | None:
    """The context LM Studio gives `model`, read from its `GET /api/v1/models` answer.

    A loaded instance reports its context length; a model that isn't loaded yet will most
    likely be loaded with LM Studio's default."""
    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    wanted = model.strip().casefold()
    exact: list[tuple[list[dict], int | None]] = []
    similar: list[tuple[list[dict], int | None]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") == "embedding":
            continue
        instances = [i for i in entry.get("loaded_instances") or () if isinstance(i, dict)]
        maximum = _positive_int(entry.get("max_context_length"))
        for instance in instances:
            if str(instance.get("id") or "").casefold() == wanted:
                return _instance_window(instance, maximum)
        key = str(entry.get("key") or "").casefold()
        if key == wanted:
            exact.append((instances, maximum))
        elif key and key.rsplit("/", 1)[-1] == wanted.rsplit("/", 1)[-1]:
            similar.append((instances, maximum))
    found = exact or (similar if len(similar) == 1 else [])
    if not found:
        return None
    instances, maximum = found[0]
    for instance in instances:
        window = _instance_window(instance, maximum)
        if window is not None:
            return window
    guess = min(LMSTUDIO_DEFAULT_CONTEXT, maximum or LMSTUDIO_DEFAULT_CONTEXT)
    return ContextWindow(tokens=guess, loaded=False, max_tokens=maximum)


def _instance_window(instance: dict, maximum: int | None) -> ContextWindow | None:
    config = instance.get("config")
    tokens = _positive_int(config.get("context_length")) if isinstance(config, dict) else None
    return ContextWindow(tokens=tokens, max_tokens=maximum) if tokens else None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def small_context_notice(server: str, model: str, window: ContextWindow) -> str | None:
    """What to tell the user when a model's context is small, or None if it isn't."""
    if window.tokens >= SMALL_CONTEXT:
        return None
    if window.loaded:
        text = f"{server} runs {model} with a {window.tokens:,}-token context"
    else:
        text = (
            f"{model} isn't loaded in {server} yet; it will most likely get a "
            f"{window.tokens:,}-token context"
        )
    text += ", so Moot shortens its prompts and the agents see less of the earlier discussion."
    maximum = window.max_tokens
    if maximum is not None and maximum <= window.tokens:
        return text + " The model can't take more; one with a longer context would do better."
    if maximum is not None and maximum < BETTER_CONTEXT:
        return text + (
            f" For better discussions, load it in {server} with its full context length "
            f"({maximum:,} tokens)."
        )
    return text + (
        f" For better discussions, load it in {server} with a larger context length, "
        f"e.g. {BETTER_CONTEXT:,}."
    )


# ------------------------------------------------------------------ the wrapper


@dataclass
class _ModelState:
    """What Moot knows about one model on one server."""

    window: ContextWindow | None = None
    checked: float = float("-inf")
    learned_chars: int | None = None  # a prompt limit learned from a "context exceeded" error
    serial: bool = False  # one request at a time
    running: int = 0
    _gate: asyncio.Semaphore | None = None
    _gate_size: int = 0
    _gate_loop: asyncio.AbstractEventLoop | None = None

    def limit(self) -> int | None:
        limits = [prompt_chars(self.window.tokens)] if self.window else []
        if self.learned_chars:
            limits.append(self.learned_chars)
        return min(limits) if limits else None

    def gate(self) -> asyncio.Semaphore | None:
        """How many requests may run at once: one for a small context (they share it),
        a few for a larger one, any number when the context is unknown."""
        if self.serial:
            size = 1
        elif self.window is not None:
            size = max(1, self.window.tokens // SMALL_CONTEXT)
        else:
            return None
        loop = asyncio.get_running_loop()
        if self._gate is None or self._gate_size != size or self._gate_loop is not loop:
            self._gate, self._gate_size, self._gate_loop = asyncio.Semaphore(size), size, loop
        return self._gate


class BudgetBook:
    """What Moot knows about each model's context. Shared by every wrapper of the same
    server, because sessions and file indexing use the same loaded model."""

    def __init__(self) -> None:
        self._models: dict[tuple[str, str], _ModelState] = {}

    def state(self, server: str, model: str) -> _ModelState:
        return self._models.setdefault((server, model), _ModelState())


SHARED_BOOK = BudgetBook()


@dataclass
class _Turn:
    crowded: bool  # other requests to the same model were running when it started


class BudgetedProvider:
    """A provider whose requests always fit the model's context (see the module docs)."""

    def __init__(
        self,
        inner: Provider,
        *,
        book: BudgetBook | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self._book = book or SHARED_BOOK
        self._clock = clock
        config = getattr(inner, "config", None)
        self._server = f"{inner.name}@{getattr(config, 'base_url', '')}"

    def __getattr__(self, name: str) -> Any:
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    async def context_window(self, model: str) -> ContextWindow | None:
        """The model's context window, when the server can tell."""
        return await self._window(self._book.state(self._server, model), model)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        state = self._book.state(self._server, request.model)
        for attempt in range(MAX_CONTEXT_RETRIES + 1):
            sent = await self._prepare(state, request)
            streamed = False
            async with self._turn(state) as turn:
                try:
                    async for event in self.inner.stream(sent):
                        streamed = streamed or isinstance(event, TextDelta)
                        yield event
                    return
                except ProviderError as exc:
                    # once text has reached the audience the answer can't be taken back
                    if streamed or attempt == MAX_CONTEXT_RETRIES or not is_context_error(exc):
                        raise
                    crowded = turn.crowded or state.running > 1
            self._learn(state, sent, crowded)

    async def complete(self, request: ChatRequest) -> ChatResult:
        state = self._book.state(self._server, request.model)
        for attempt in range(MAX_CONTEXT_RETRIES + 1):
            sent = await self._prepare(state, request)
            async with self._turn(state) as turn:
                try:
                    return await self.inner.complete(sent)
                except ProviderError as exc:
                    if attempt == MAX_CONTEXT_RETRIES or not is_context_error(exc):
                        raise
                    crowded = turn.crowded or state.running > 1
            self._learn(state, sent, crowded)
        raise AssertionError("unreachable")

    async def list_models(self) -> list[ModelInfo]:
        return await self.inner.list_models()

    async def aclose(self) -> None:
        await self.inner.aclose()

    # ----------------------------------------------------------------- helpers

    async def _window(self, state: _ModelState, model: str) -> ContextWindow | None:
        probe = getattr(self.inner, "context_window", None)
        if probe is None:
            return state.window
        now = self._clock()
        if now - state.checked < PROBE_SECONDS:
            return state.window
        try:
            window = await asyncio.wait_for(probe(model), PROBE_TIMEOUT)
        except Exception as exc:  # finding out is a bonus; it must never break a request
            log.debug("could not read the context size of %s: %s", model, exc)
            window = None
        if window is not None:
            state.window = window
        # a model that isn't loaded yet is checked again next time: it may load with more
        state.checked = now if window is None or window.loaded else float("-inf")
        return state.window

    async def _prepare(self, state: _ModelState, request: ChatRequest) -> ChatRequest:
        await self._window(state, request.model)
        limit = state.limit()
        messages = fit(request.messages, limit) if limit else render_messages(request.messages)
        return replace(request, messages=messages)

    @asynccontextmanager
    async def _turn(self, state: _ModelState) -> AsyncIterator[_Turn]:
        gate = state.gate()
        if gate is not None:
            await gate.acquire()
        turn = _Turn(crowded=state.running > 0)
        state.running += 1
        try:
            yield turn
        finally:
            state.running -= 1
            if gate is not None:
                gate.release()

    def _learn(self, state: _ModelState, sent: ChatRequest, crowded: bool) -> None:
        """After a "context exceeded" error: if other requests shared the context, run one
        at a time from now on; otherwise also shorten the prompts."""
        was_serial, state.serial = state.serial, True
        if crowded and not was_serial:
            log.info("%s: requests to one model now run one at a time", self.name)
            return
        chars = sum(len(m.content) for m in sent.messages)
        lower = max(MIN_PROMPT_CHARS, int(chars * 0.7))
        state.learned_chars = min(state.learned_chars or lower, lower)
        log.info("%s: prompts now kept under %d characters", self.name, state.learned_chars)


class BudgetedEmbedder(BudgetedProvider):
    """A budgeted provider that also makes embeddings (passed straight through)."""

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return await self.inner.embed(model, texts)  # type: ignore[attr-defined]


def budgeted(provider: Provider, *, book: BudgetBook | None = None) -> BudgetedProvider:
    """Wrap a provider so its requests fit the model's context."""
    if isinstance(provider, BudgetedProvider):
        return provider
    kind = BudgetedEmbedder if isinstance(provider, Embedder) else BudgetedProvider
    return kind(provider, book=book)
