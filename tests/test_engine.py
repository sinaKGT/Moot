"""Tests for the discussion engine, using a fake provider."""

from __future__ import annotations

import asyncio

import pytest
from fakes import FakeProvider, round_of, speaker_of

from moot.core import (
    ModelRef,
    Persona,
    Round,
    RoundPlan,
    RoundType,
    SessionConfig,
    SessionState,
    SessionStatus,
    SpeakerKind,
)
from moot.core.events import (
    ErrorEvent,
    SessionEnded,
    SessionPaused,
    SessionResumed,
    TokenDelta,
    TurnCompleted,
    TurnStarted,
)
from moot.engine import DiscussionEngine, ProviderPool
from moot.providers import ProviderConnectionError

MODEL = ModelRef(provider="fake", model="fake-model")


def make_state(rounds: list[Round] | None = None) -> SessionState:
    config = SessionConfig(
        topic="Should cities ban cars?",
        agents=[Persona(name="Ava", role="Advocate"), Persona(name="Ben", role="Critic")],
        round_plan=RoundPlan(
            rounds=rounds or [Round(type=RoundType.OPENING), Round(type=RoundType.CHALLENGE)]
        ),
        default_model=MODEL,
    )
    return SessionState.new(config)


def make_engine(state, provider=None, **kwargs):
    provider = provider or FakeProvider()
    pool = ProviderPool(factory=lambda preset_id: provider)
    return DiscussionEngine(state, providers=pool, **kwargs), provider


def agent_texts(state):
    return [m.text for m in state.messages if m.speaker_kind is SpeakerKind.AGENT]


def run(coro):
    return asyncio.run(coro)


def test_runs_every_round_and_records_messages():
    state = make_state()
    engine, _ = make_engine(state)

    run(engine.run())

    assert agent_texts(state) == ["Ava round 1", "Ben round 1", "Ava round 2", "Ben round 2"]
    assert [m.round_index for m in state.messages] == [0, 0, 1, 1]
    assert all(m.usage and m.usage.completion_tokens == 5 for m in state.messages)
    assert state.status is SessionStatus.FINISHED

    history = engine.events.history
    assert [e.seq for e in history] == list(range(1, len(history) + 1))
    assert engine.events.closed
    flow = [e.type for e in history if e.type != "token_delta"]
    turn = ["turn_started", "turn_completed"]
    assert flow == (
        ["session_started", "round_started", *turn, *turn, "round_ended"]
        + ["round_started", *turn, *turn, "round_ended", "session_ended"]
    )
    assert history[-1].reason == "completed"


def test_tokens_stream_before_the_turn_completes():
    state = make_state()
    engine, _ = make_engine(state)
    run(engine.run())

    first_turn = engine.events.history[2:6]
    assert isinstance(first_turn[0], TurnStarted)
    assert [e.text for e in first_turn[1:3]] == ["Ava ", "round 1"]
    assert all(isinstance(e, TokenDelta) for e in first_turn[1:3])
    assert isinstance(first_turn[3], TurnCompleted)
    assert first_turn[3].message.id == first_turn[0].message_id


def test_blind_round_hides_the_others_answers():
    state = make_state()
    engine, provider = make_engine(state)
    run(engine.run())

    prompts = {(speaker_of(r), round_of(r)): r.messages[-1].content for r in provider.requests}
    assert "Nobody has spoken yet." in prompts[("Ben", 1)]
    assert "Ava round 1" not in prompts[("Ben", 1)]
    assert "Ava round 1" in prompts[("Ava", 2)]
    assert "Ben round 1" in prompts[("Ava", 2)]
    assert "Ava round 2" in prompts[("Ben", 2)]


def test_several_turns_per_agent():
    state = make_state([Round(type=RoundType.FREE, turns_per_agent=2)])
    engine, provider = make_engine(state)
    run(engine.run())

    assert len(agent_texts(state)) == 4
    assert "This is your turn 2 of 2" in provider.requests[-1].messages[-1].content


def test_name_prefix_is_removed_from_the_answer():
    state = make_state([Round(type=RoundType.OPENING)])
    engine, _ = make_engine(state, FakeProvider(lambda request: ["Av", "a: Hel", "lo"]))
    run(engine.run())

    assert agent_texts(state)[0] == "Hello"
    first_deltas = []
    for event in engine.events.history:
        if isinstance(event, TokenDelta):
            first_deltas.append(event.text)
        if isinstance(event, TurnCompleted):
            break
    assert "".join(first_deltas) == "Hello"


def test_failed_turn_is_reported_and_the_discussion_continues():
    def script(request):
        if speaker_of(request) == "Ben" and round_of(request) == 1:
            return ProviderConnectionError("Fake", "can't reach the server")
        return ["fine"]

    state = make_state()
    engine, _ = make_engine(state, FakeProvider(script))
    run(engine.run())

    ben = state.config.agents[1]
    errors = [e for e in engine.events.history if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].agent_id == ben.id
    assert errors[0].message_id is not None
    notices = [m.text for m in state.messages if m.speaker_kind is SpeakerKind.SYSTEM]
    assert notices == ["Ben could not answer (Fake: can't reach the server)."]
    assert agent_texts(state) == ["fine", "fine", "fine"]
    assert engine.events.history[-1].reason == "completed"


def test_too_many_failures_end_the_discussion():
    state = make_state()
    failing = FakeProvider(lambda request: ProviderConnectionError("Fake", "down"))
    engine, _ = make_engine(state, failing, max_consecutive_failures=3)
    run(engine.run())

    assert len(failing.requests) == 3
    assert engine.events.history[-1].reason == "error"
    assert state.messages[-1].text.startswith("Too many failed turns")
    assert state.status is SessionStatus.FINISHED


def test_empty_answer_counts_as_a_failure():
    state = make_state([Round(type=RoundType.OPENING)])
    engine, _ = make_engine(state, FakeProvider(lambda request: ["   "]))
    run(engine.run())

    assert agent_texts(state) == []
    assert all("empty answer" in e.message for e in engine.events.history if e.type == "error")


def test_stop_finishes_after_the_current_turn():
    state = make_state()
    engine, _ = make_engine(state)

    async def scenario():
        async def watch():
            async for event in engine.events.subscribe():
                if isinstance(event, TurnCompleted):
                    engine.stop()

        watcher = asyncio.create_task(watch())
        await engine.run()
        await watcher

    run(scenario())
    assert agent_texts(state) == ["Ava round 1"]
    assert engine.events.history[-1].reason == "stopped"


def test_pause_and_resume():
    state = make_state()
    engine, _ = make_engine(state)

    async def scenario():
        async def watch():
            paused = False
            async for event in engine.events.subscribe():
                if isinstance(event, TurnCompleted) and not paused:
                    paused = True
                    engine.pause()
                    asyncio.get_running_loop().call_later(0.02, engine.resume)

        watcher = asyncio.create_task(watch())
        await engine.run()
        await watcher

    run(scenario())
    types = [e.type for e in engine.events.history]
    paused_at, resumed_at = types.index("session_paused"), types.index("session_resumed")
    second_turn = [i for i, t in enumerate(types) if t == "turn_started"][1]
    assert paused_at < resumed_at < second_turn
    assert len(agent_texts(state)) == 4
    assert isinstance(engine.events.history[paused_at], SessionPaused)
    assert isinstance(engine.events.history[resumed_at], SessionResumed)


def test_vote_round_is_skipped_with_a_notice():
    state = make_state([Round(type=RoundType.OPENING), Round(type=RoundType.VOTE)])
    engine, _ = make_engine(state)
    run(engine.run())

    assert state.messages[-1].speaker_kind is SpeakerKind.SYSTEM
    assert "Vote rounds are not available yet" in state.messages[-1].text
    assert engine.events.history[-1].reason == "completed"


def test_a_session_runs_only_once():
    state = make_state([Round(type=RoundType.OPENING)])
    engine, _ = make_engine(state)
    run(engine.run())
    with pytest.raises(RuntimeError, match="already been run"):
        run(engine.run())


def test_unknown_provider_fails_before_starting():
    state = make_state()
    state.config.default_model = ModelRef(provider="nope", model="x")
    engine = DiscussionEngine(state)
    with pytest.raises(ValueError, match="Unknown provider"):
        run(engine.run())
    assert state.status is SessionStatus.SETUP
    assert engine.events.history == []


def test_late_subscriber_gets_the_whole_history():
    state = make_state([Round(type=RoundType.OPENING)])
    engine, _ = make_engine(state)
    run(engine.run())

    async def collect():
        return [event async for event in engine.events.subscribe()]

    events = run(collect())
    assert events == engine.events.history
    assert isinstance(events[-1], SessionEnded)


def test_provider_pool_shares_and_closes_providers():
    created = []

    def factory(preset_id):
        provider = FakeProvider()
        created.append((preset_id, provider))
        return provider

    pool = ProviderPool(factory=factory)
    first = pool.get(ModelRef(provider="ollama", model="a"))
    assert pool.get(ModelRef(provider="ollama", model="b")) is first
    pool.get(ModelRef(provider="openrouter", model="c"))
    run(pool.aclose())

    assert [preset for preset, _ in created] == ["ollama", "openrouter"]
    assert all(provider.closed for _, provider in created)
