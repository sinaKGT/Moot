"""Tests for keeping requests inside a model's context window."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fakes import FakeProvider, default_script, is_graph_request, moderator_reply

from moot.core import ModelRef, Persona, Round, RoundPlan, RoundType, SessionConfig, SessionState
from moot.core.events import EngineNotice, ErrorEvent
from moot.engine import DiscussionEngine, ProviderPool
from moot.providers import (
    BudgetBook,
    ChatRequest,
    ContextWindow,
    Message,
    ProviderAuthError,
    ProviderResponseError,
    TextDelta,
    budgeted,
    render,
    small_context_notice,
    trimmable,
)
from moot.providers.budget import (
    MIN_PROMPT_CHARS,
    BudgetedEmbedder,
    fit,
    is_context_error,
    lmstudio_window,
    prompt_chars,
)

LMSTUDIO_ERROR = (
    'Engine protocol predict stream returned an error: {"code":500,"message":"Context size '
    'has been exceeded.","type":"server_error"}'
)


def run(coro):
    return asyncio.run(coro)


def total(messages: list[Message]) -> int:
    return sum(len(m.content) for m in messages)


# ------------------------------------------------------------------ marked regions


def test_marked_regions_render_like_plain_text():
    text = "Header:\n" + trimmable(["a", "b", "c"], joiner="\n\n") + "\nEnd"
    assert render(text) == "Header:\na\n\nb\n\nc\nEnd"
    assert trimmable([]) == ""
    assert render(trimmable([trimmable(["x", "y"]), "z"])) == "x\ny\nz"  # nested: flattened
    assert render(trimmable(["sneaky\x1d\x1f\x1etext"])) == "sneakytext"


def test_fit_leaves_a_prompt_that_fits_alone():
    messages = [Message("system", "You are Ava."), Message("user", trimmable(["one", "two"]))]
    assert fit(messages, 1000) == [Message("system", "You are Ava."), Message("user", "one\ntwo")]


def test_fit_drops_the_lowest_priority_and_oldest_items_first():
    earlier = trimmable([f"- point {i} " + "x" * 40 for i in range(10)])
    recent = trimmable(
        [f"Speaker {i}: " + "y" * 80 for i in range(6)], joiner="\n\n", priority=1, keep=2
    )
    content = f"Earlier:\n{earlier}\n\nRecent:\n\n{recent}\n\nIt is your turn."
    full = render(content)

    shorter = fit([Message("user", content)], len(full) - 200)[0].content
    assert len(shorter) <= len(full) - 200
    assert "point 0 " not in shorter and "point 9 " in shorter  # oldest points go first
    assert "earlier parts left out to fit the model's context" in shorter
    assert "Speaker 0:" in shorter  # the messages are untouched while points remain

    tight = fit([Message("user", content)], 400)[0].content
    assert len(tight) <= 400
    assert "Speaker 5:" in tight and "Speaker 4:" in tight  # the newest two are kept
    assert "Speaker 3:" not in tight
    assert tight.endswith("It is your turn.")


def test_fit_drops_the_last_items_of_a_ranked_list():
    facts = [f"- fact {i}: " + "f" * 60 for i in range(8)]
    content = "Facts:\n" + trimmable(facts, drop="last") + "\nUse it."
    shorter = fit([Message("user", content)], 300)[0].content
    assert len(shorter) <= 300
    assert "fact 0" in shorter and "fact 7" not in shorter
    assert "more left out to fit the model's context" in shorter
    assert shorter.endswith("Use it.")


def test_fit_cuts_the_middle_of_a_long_message_as_a_last_resort():
    long = "START " + "z" * 5000 + " END"
    messages = [Message("system", "Rules."), Message("user", long)]
    fitted = fit(messages, 1200)
    assert total(fitted) <= 1200
    assert fitted[0].content == "Rules."
    assert fitted[1].content.startswith("START") and fitted[1].content.endswith("END")
    assert "left out to fit the model's context" in fitted[1].content


def test_prompt_room_leaves_space_for_the_answer():
    assert 7000 < prompt_chars(4096) < 4096 * 3
    assert prompt_chars(32768) > prompt_chars(8192) > prompt_chars(4096)
    assert prompt_chars(500) == MIN_PROMPT_CHARS


@pytest.mark.parametrize(
    "message",
    [
        LMSTUDIO_ERROR,
        "This model's maximum context length is 8192 tokens. However, you requested 9000.",
        "the request exceeds the available context size, try increasing it",
        "context_length_exceeded",
        "prompt is too long: 205000 tokens > 200000 maximum",
    ],
)
def test_context_errors_are_recognised(message):
    assert is_context_error(ProviderResponseError("LM Studio", message))


def test_other_errors_are_not_context_errors():
    assert not is_context_error(ProviderResponseError("LM Studio", "HTTP 500. model crashed"))
    assert not is_context_error(ProviderAuthError("X", "context size exceeded"))
    assert not is_context_error(ValueError("context size has been exceeded"))


# ------------------------------------------------------------------ LM Studio's report

LMSTUDIO_MODELS = {
    "models": [
        {
            "type": "llm",
            "key": "google/gemma-4-26b-a4b",
            "max_context_length": 131072,
            "loaded_instances": [
                {"id": "google/gemma-4-26b-a4b", "config": {"context_length": 4096}},
                {"id": "google/gemma-4-26b-a4b:2", "config": {"context_length": 16384}},
            ],
        },
        {"type": "llm", "key": "deepseek/r1", "max_context_length": 2048, "loaded_instances": []},
        {"type": "embedding", "key": "nomic-embed", "loaded_instances": []},
    ]
}


def test_lmstudio_reports_the_loaded_context():
    assert lmstudio_window(LMSTUDIO_MODELS, "google/gemma-4-26b-a4b") == ContextWindow(
        4096, loaded=True, max_tokens=131072
    )
    assert lmstudio_window(LMSTUDIO_MODELS, "google/gemma-4-26b-a4b:2").tokens == 16384
    assert lmstudio_window(LMSTUDIO_MODELS, "gemma-4-26b-a4b").tokens == 4096  # short name


def test_lmstudio_guesses_for_a_model_not_loaded_yet():
    assert lmstudio_window(LMSTUDIO_MODELS, "deepseek/r1") == ContextWindow(
        2048, loaded=False, max_tokens=2048
    )
    assert lmstudio_window(LMSTUDIO_MODELS, "nomic-embed") is None
    assert lmstudio_window(LMSTUDIO_MODELS, "unknown/model") is None
    assert lmstudio_window({"data": []}, "x") is None


def test_the_provider_asks_lmstudio_for_the_context(make_provider):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=LMSTUDIO_MODELS)

    provider = make_provider(handler, base_url="http://localhost:1234/v1", context_api="lmstudio")
    window = run(provider.context_window("google/gemma-4-26b-a4b"))
    assert window is not None and window.tokens == 4096
    assert seen == ["http://localhost:1234/api/v1/models"]

    other = make_provider(handler)  # not LM Studio: nothing to ask
    assert run(other.context_window("google/gemma-4-26b-a4b")) is None
    assert len(seen) == 1

    broken = make_provider(lambda r: httpx.Response(404), context_api="lmstudio")
    assert run(broken.context_window("google/gemma-4-26b-a4b")) is None


def test_small_context_notice():
    loaded = small_context_notice("LM Studio", "qwen", ContextWindow(4096, max_tokens=40960))
    assert loaded is not None
    assert "4,096-token context" in loaded and "16,384" in loaded
    waiting = small_context_notice("LM Studio", "qwen", ContextWindow(4096, loaded=False))
    assert waiting is not None and "isn't loaded" in waiting
    capped = small_context_notice("LM Studio", "tiny", ContextWindow(4096, max_tokens=4096))
    assert capped is not None and "can't take more" in capped
    assert small_context_notice("LM Studio", "big", ContextWindow(32768)) is None


# ------------------------------------------------------------------ the wrapper


class TightProvider(FakeProvider):
    """A fake server that rejects prompts longer than `limit` characters, like LM Studio."""

    name = "Tight"

    def __init__(self, limit: int, window: ContextWindow | None = None, answer: str = "ok") -> None:
        super().__init__()
        self.limit = limit
        self.window = window
        self.answer = answer
        self.probes = 0

    async def stream(self, request):
        self.requests.append(request)
        if total(list(request.messages)) > self.limit:
            raise ProviderResponseError(self.name, LMSTUDIO_ERROR)
        yield TextDelta(self.answer)

    async def context_window(self, model):
        self.probes += 1
        return self.window


def long_request(items: int = 40) -> ChatRequest:
    said = trimmable([f"Speaker {i}: " + "w" * 100 for i in range(items)], joiner="\n\n")
    return ChatRequest(
        model="m", messages=[Message("system", "You are Ava."), Message("user", said + "\nGo.")]
    )


def test_a_rejected_request_is_retried_shorter():
    inner = TightProvider(limit=3300)
    provider = budgeted(inner, book=BudgetBook())

    result = run(provider.complete(long_request()))

    assert result.text == "ok"
    assert len(inner.requests) == 2
    first, second = (total(list(r.messages)) for r in inner.requests)
    assert second < first and second <= 3300
    assert "\x1d" not in inner.requests[0].messages[1].content  # marks never reach a server
    run(provider.complete(long_request()))  # what it learned holds for the next request
    assert len(inner.requests) == 3


def test_streamed_text_is_never_retried():
    class Breaks(TightProvider):
        async def stream(self, request):
            self.requests.append(request)
            yield TextDelta("half an answer")
            raise ProviderResponseError(self.name, LMSTUDIO_ERROR)

    inner = Breaks(limit=0)
    provider = budgeted(inner, book=BudgetBook())

    async def consume():
        return [e async for e in provider.stream(long_request())]

    with pytest.raises(ProviderResponseError):
        run(consume())
    assert len(inner.requests) == 1


def test_a_reported_window_shapes_every_prompt_and_runs_one_request_at_a_time():
    window = ContextWindow(4096)
    inner = TightProvider(limit=prompt_chars(4096), window=window)
    running = peak = 0

    original = inner.stream

    async def counting(request):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.sleep(0.01)
            async for event in original(request):
                yield event
        finally:
            running -= 1

    inner.stream = counting  # type: ignore[method-assign]
    provider = budgeted(inner, book=BudgetBook())

    async def several():
        return await asyncio.gather(*(provider.complete(long_request(200)) for _ in range(3)))

    results = run(several())
    assert [r.text for r in results] == ["ok"] * 3
    assert len(inner.requests) == 3  # every prompt fitted the first time
    assert peak == 1  # they shared one small context, so they took turns
    assert inner.probes == 1  # the size is remembered for a while


def test_a_model_not_loaded_yet_is_checked_again():
    inner = TightProvider(limit=10**6, window=ContextWindow(4096, loaded=False))
    provider = budgeted(inner, book=BudgetBook())
    run(provider.complete(long_request(2)))
    run(provider.complete(long_request(2)))
    assert inner.probes == 2


def test_requests_crowding_one_context_are_queued_rather_than_shortened():
    class Shared(TightProvider):
        """Fails while another request is running, like LM Studio's shared context."""

        running = 0

        async def stream(self, request):
            self.requests.append(request)
            self.running += 1
            try:
                await asyncio.sleep(0.01)
                if self.running > 1:
                    raise ProviderResponseError(self.name, LMSTUDIO_ERROR)
                yield TextDelta("ok")
            finally:
                self.running -= 1

    inner = Shared(limit=10**6)
    provider = budgeted(inner, book=BudgetBook())
    request = long_request(5)

    async def both():
        return await asyncio.gather(provider.complete(request), provider.complete(request))

    results = run(both())
    assert [r.text for r in results] == ["ok", "ok"]
    sizes = {total(list(r.messages)) for r in inner.requests}
    assert len(sizes) == 1  # nothing was shortened


def test_embedders_stay_embedders():
    class Embeds(FakeProvider):
        async def embed(self, model, texts):
            return [[1.0] for _ in texts]

    wrapped = budgeted(Embeds())
    assert isinstance(wrapped, BudgetedEmbedder)
    assert run(wrapped.embed("e", ["a", "b"])) == [[1.0], [1.0]]
    assert budgeted(wrapped) is wrapped
    assert not isinstance(budgeted(FakeProvider()), BudgetedEmbedder)


# ------------------------------------------------------------------ a whole discussion


def test_a_long_discussion_fits_a_small_local_model():
    limit = prompt_chars(4096)
    answer = "I think " + "this matters a great deal because of many reasons. " * 25
    inner = TightProvider(limit, ContextWindow(4096, max_tokens=32768), answer)
    config = SessionConfig(
        topic="Should cities ban cars?",
        agents=[Persona(name=n, role="Debater") for n in ("Ava", "Ben", "Cy")],
        round_plan=RoundPlan(
            rounds=[
                Round(type=RoundType.OPENING),
                Round(type=RoundType.CHALLENGE, turns_per_agent=3),
                Round(type=RoundType.CHALLENGE, turns_per_agent=3),
            ]
        ),
        default_model=ModelRef(provider="lmstudio", model="qwen"),
    )
    state = SessionState.new(config)
    pool = ProviderPool(factory=lambda preset: inner, book=BudgetBook())
    engine = DiscussionEngine(state, providers=pool, moderator_enabled=False, build_graph=False)
    events = []
    engine.events.listen(events.append)

    run(engine.run())

    assert not [e for e in events if isinstance(e, ErrorEvent)]
    assert all(total(list(r.messages)) <= limit for r in inner.requests)
    assert len([m for m in state.messages if m.text]) == 21
    notices = [e.message for e in events if isinstance(e, EngineNotice)]
    assert len(notices) == 1 and "4,096-token context" in notices[0]
    late = render(inner.requests[-1].messages[-1].content)
    assert "left out to fit the model's context" in late


def test_lmstudio_error_chunks_are_retried_end_to_end(make_provider, sse, chunk):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if sum(len(m["content"]) for m in body["messages"]) > 2500:
            error = {"error": LMSTUDIO_ERROR}
            return httpx.Response(200, content=f"data: {json.dumps(error)}\n\n".encode())
        return httpx.Response(200, content=sse(chunk("fine"), chunk(finish="stop")))

    provider = budgeted(make_provider(handler, name="LM Studio"), book=BudgetBook())
    result = run(provider.complete(long_request()))
    assert result.text == "fine"


def test_moderator_and_argument_map_fit_a_small_model_too():
    limit = prompt_chars(4096)
    long_answer = "My view: " + "cars crowd streets and cost lives, so cities should act. " * 30

    def script(request):
        reply = default_script(request)
        if moderator_reply(request) is not None or is_graph_request(request):
            return reply
        return [long_answer]

    class Small(FakeProvider):
        name = "Small"

        def __init__(self) -> None:
            super().__init__(script)
            self.too_long = 0

        async def stream(self, request):
            if total(list(request.messages)) > limit:
                self.too_long += 1
                raise ProviderResponseError(self.name, LMSTUDIO_ERROR)
            async for event in super().stream(request):
                yield event

        async def context_window(self, model):
            return ContextWindow(4096)

    inner = Small()
    config = SessionConfig(
        topic="Should cities ban cars?",
        agents=[Persona(name=n, role="Debater") for n in ("Ava", "Ben", "Cy", "Dee")],
        round_plan=RoundPlan(
            rounds=[
                Round(type=RoundType.OPENING),
                Round(type=RoundType.CHALLENGE, turns_per_agent=2),
                Round(type=RoundType.CLOSING),
            ]
        ),
        default_model=ModelRef(provider="lmstudio", model="qwen"),
    )
    state = SessionState.new(config)
    pool = ProviderPool(factory=lambda preset: inner, book=BudgetBook())
    engine = DiscussionEngine(state, providers=pool)
    events = []
    engine.events.listen(events.append)

    run(engine.run())

    assert inner.too_long == 0
    assert not [e for e in events if isinstance(e, ErrorEvent)]
    assert state.end_reason == "completed"
    assert len(state.graph.nodes) > 10  # the argument map kept up
