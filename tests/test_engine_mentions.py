"""Tests for @mentions: immediate replies, the loop guard, and messages from the user."""

from __future__ import annotations

import asyncio
import io

import pytest
from fakes import FakeProvider, default_script, speaker_of

from moot.console import ConsoleRenderer
from moot.core import (
    MODERATOR_ID,
    USER_ID,
    HumanModerator,
    ModelRef,
    Persona,
    Round,
    RoundPlan,
    RoundType,
    RulePreset,
    SessionConfig,
    SessionRules,
    SessionState,
    SessionStatus,
    SpeakerKind,
    SpeakingOrder,
)
from moot.core.events import TurnCompleted, TurnStarted
from moot.demo import listen_to_terminal
from moot.engine import DiscussionEngine, ProviderPool
from moot.engine.rule_checks import check_rules
from moot.memory import GraphBuilder

MODEL = ModelRef(provider="fake", model="fake-model")


def qa() -> Round:
    return Round(type=RoundType.QA, speaking_order=SpeakingOrder.FIXED)


QA = qa()


def make_state(rounds=None, names=("Ava", "Ben", "Cleo"), **config) -> SessionState:
    settings = {
        "topic": "Should cities ban cars?",
        "agents": [Persona(name=name) for name in names],
        "round_plan": RoundPlan(rounds=rounds or [qa()]),
        "rules": SessionRules(presets=[]),
        "default_model": MODEL,
    }
    settings.update(config)
    return SessionState.new(SessionConfig(**settings))


def make_engine(state, script=None, **kwargs):
    provider = FakeProvider(script)
    kwargs.setdefault("moderator_enabled", False)
    kwargs.setdefault("build_graph", False)
    pool = ProviderPool(factory=lambda preset_id: provider)
    return DiscussionEngine(state, providers=pool, **kwargs), provider


def is_reply(request) -> bool:
    return "asked you directly" in request.messages[-1].content


def scripted(lines: dict[str, str], replies: dict[str, str] | None = None):
    """Answers by speaker for normal turns, and (optionally) for replies."""

    def script(request):
        if is_reply(request):
            return [(replies or {}).get(speaker_of(request), f"{speaker_of(request)} answers")]
        return [lines.get(speaker_of(request), f"{speaker_of(request)} speaks")]

    return script


def spoken(state):
    """(speaker name, reply-to speaker name or None) for each agent or user message."""
    names = {a.id: a.name for a in state.config.agents} | {USER_ID: "User", MODERATOR_ID: "Mod"}
    by_id = {m.id: m for m in state.messages}
    return [
        (names[m.speaker_id], names[by_id[m.reply_to].speaker_id] if m.reply_to else None)
        for m in state.messages
        if m.speaker_kind is not SpeakerKind.SYSTEM
    ]


def run(coro):
    return asyncio.run(coro)


def test_a_mentioned_agent_answers_straight_away():
    state = make_state()
    engine, provider = make_engine(state, scripted({"Ava": "@Cleo why would shops survive?"}))

    run(engine.run())

    assert spoken(state) == [("Ava", None), ("Cleo", "Ava"), ("Ben", None), ("Cleo", None)]
    question, reply = state.messages[0], state.messages[1]
    assert question.mentions == [state.config.agents[2].id]
    assert reply.reply_to == question.id
    started = [e for e in engine.events.history if isinstance(e, TurnStarted)]
    assert started[1].reply_to == question.id
    reply_prompt = provider.requests[1].messages[-1].content
    assert 'Ava asked you directly: "@Cleo why would shops survive?"' in reply_prompt
    assert "Answer Ava first" in reply_prompt


def test_normal_turns_in_mention_rounds_explain_the_at_sign():
    state = make_state(rounds=[qa(), Round(type=RoundType.CHALLENGE)])
    engine, provider = make_engine(state)
    run(engine.run())
    prompts = [r.messages[-1].content for r in provider.requests]
    assert "@Name" in prompts[0]  # Q&A round
    assert "@Name" not in prompts[-1]  # challenge round


@pytest.mark.parametrize(
    "current",
    [
        Round(type=RoundType.CHALLENGE),  # mentions not allowed
        Round(type=RoundType.OPENING, mentions_allowed=True),  # blind
    ],
)
def test_no_immediate_replies_where_mentions_are_off(current):
    state = make_state(rounds=[current], names=("Ava", "Ben"))
    engine, _ = make_engine(state, scripted({"Ava": "@Ben where is the evidence?"}))
    run(engine.run())
    assert spoken(state) == [("Ava", None), ("Ben", None)]
    assert state.messages[0].mentions == [state.config.agents[1].id]


def ping_pong(request):
    other = "Ben" if speaker_of(request) == "Ava" else "Ava"
    return [f"@{other} what about you?"]


def test_reply_chain_stops_after_one_answer_back():
    state = make_state(names=("Ava", "Ben"))
    engine, _ = make_engine(state, ping_pong, max_replies_per_round=10)
    run(engine.run())
    assert spoken(state) == [
        ("Ava", None),
        ("Ben", "Ava"),
        ("Ava", "Ben"),  # one answer back, then the chain stops
        ("Ben", None),
        ("Ava", "Ben"),
        ("Ben", "Ava"),
    ]


def test_replies_per_round_are_capped():
    state = make_state(names=("Ava", "Ben"))
    engine, _ = make_engine(state, ping_pong)  # default cap: the round's 2 normal turns
    run(engine.run())
    assert spoken(state) == [("Ava", None), ("Ben", "Ava"), ("Ava", "Ben"), ("Ben", None)]


def test_the_cap_resets_each_round():
    state = make_state(rounds=[qa(), qa()], names=("Ava", "Ben"))
    engine, _ = make_engine(state, ping_pong, max_reply_chain=1)
    run(engine.run())
    replies = [m.round_index for m in state.messages if m.reply_to]
    assert replies == [0, 0, 1, 1]


def test_the_user_can_ask_any_agent_even_in_a_blind_round():
    state = make_state(rounds=[Round(type=RoundType.OPENING)], names=("Ava", "Ben"))
    engine, provider = make_engine(state, scripted({}))
    engine.say("@Ben what do you think so far?")

    run(engine.run())

    assert spoken(state) == [("User", None), ("Ben", "User"), ("Ava", None), ("Ben", None)]
    question = state.messages[0]
    assert question.speaker_kind is SpeakerKind.USER
    assert question.mentions == [state.config.agents[1].id]
    assert 'User asked you directly: "@Ben what do you think so far?"' in (
        provider.requests[0].messages[-1].content
    )


def test_user_messages_without_mentions_are_just_posted():
    state = make_state(names=("Ava", "Ben"))
    engine, _ = make_engine(state)
    engine.say("  Please keep it civil.  ")
    run(engine.run())
    assert state.messages[0].text == "Please keep it civil."
    assert spoken(state) == [("User", None), ("Ava", None), ("Ben", None)]


def test_say_can_address_agents_by_name_or_id():
    state = make_state(names=("Ava", "Ben"))
    ava, ben = state.config.agents
    engine, _ = make_engine(state)
    engine.say("What would change your mind?", to=["Ava", ben.id])
    run(engine.run())
    assert state.messages[0].mentions == [ava.id, ben.id]
    assert spoken(state)[:3] == [("User", None), ("Ava", "User"), ("Ben", "User")]


def test_say_rejects_bad_input_and_finished_sessions():
    state = make_state(names=("Ava", "Ben"))
    engine, _ = make_engine(state)
    with pytest.raises(ValueError, match="empty"):
        engine.say("   ")
    with pytest.raises(ValueError, match="Nobody"):
        engine.say("Hi", to=["Nobody"])
    run(engine.run())
    with pytest.raises(RuntimeError, match="finished"):
        engine.say("Too late")


def test_the_user_can_talk_to_an_agent_while_paused():
    state = make_state(names=("Ava", "Ben"))
    engine, _ = make_engine(state)
    status_during_reply = []

    async def scenario():
        async def control():
            async for event in engine.events.subscribe():
                if not isinstance(event, TurnCompleted):
                    continue
                message = event.message
                if message.speaker_kind is SpeakerKind.AGENT and len(state.messages) == 1:
                    engine.pause()
                    engine.say("@Ben, a quick question while we wait")
                elif message.reply_to:
                    status_during_reply.append(state.status)
                    engine.resume()

        task = asyncio.create_task(control())
        await engine.run()
        await task

    run(scenario())
    assert status_during_reply == [SessionStatus.PAUSED]
    assert spoken(state) == [("Ava", None), ("User", None), ("Ben", "User"), ("Ben", None)]
    assert state.status is SessionStatus.FINISHED


def test_stop_while_paused_drops_unsent_messages():
    state = make_state(names=("Ava", "Ben"))
    engine, _ = make_engine(state)

    async def scenario():
        async def control():
            async for event in engine.events.subscribe():
                if isinstance(event, TurnCompleted) and len(state.messages) == 1:
                    engine.pause()
                    engine.stop()
                    engine.say("@Ben are you there?")

        task = asyncio.create_task(control())
        await engine.run()
        await task

    run(scenario())
    assert spoken(state) == [("Ava", None)]


def test_a_human_moderator_speaks_as_the_moderator():
    human = HumanModerator(display_name="Sina", role="Judge")
    state = make_state(names=("Ava", "Ben"), moderator=human, graph_builder_model=MODEL)
    engine, provider = make_engine(state)
    engine.say("@Ava please be specific")
    run(engine.run())
    question = state.messages[0]
    assert (question.speaker_id, question.speaker_kind) == (MODERATOR_ID, SpeakerKind.MODERATOR)
    assert 'Sina asked you directly: "@Ava please be specific"' in (
        provider.requests[0].messages[-1].content
    )


def test_user_messages_and_replies_go_into_the_argument_graph():
    state = make_state(names=("Ava", "Ben"))
    engine, provider = make_engine(state, build_graph=True)
    engine.say("@Ava how would deliveries work?")
    run(engine.run())

    graph_prompts = [
        r.messages[-1].content for r in provider.requests if "New message from" in str(r.messages)
    ]
    assert graph_prompts[0].count("New message from User") == 1
    assert "This message answers User, who asked:" in graph_prompts[1]
    assert "how would deliveries work?" in graph_prompts[1]


def test_graph_prompt_mentions_the_question_a_reply_answers():
    state = make_state(names=("Ava", "Ben"))
    engine, _ = make_engine(state, scripted({"Ava": "@Ben where is the evidence?"}))
    run(engine.run())
    reply = next(m for m in state.messages if m.reply_to)
    messages, _ = GraphBuilder().build_prompt(state, reply)
    assert 'This message answers Ava, who asked:\n"""\n@Ben where is the evidence?' in (
        messages[-1].content
    )


def test_replies_are_not_held_to_the_answer_first_rule():
    state = make_state(rules=SessionRules(presets=[RulePreset.ANSWER_FIRST]))
    config = state.config
    assert check_rules(config, QA, "Because buses would replace them.")
    assert check_rules(config, QA, "Because buses would replace them.", reply=True) == []


def test_console_shows_who_a_reply_answers():
    state = make_state(names=("Ava", "Ben"))
    engine, _ = make_engine(state, scripted({"Ava": "@Ben really?"}))
    engine.say("@Ava hello")
    out = io.StringIO()
    renderer = ConsoleRenderer(state.config, out=out, color=False, state=state)

    async def scenario():
        follower = asyncio.create_task(renderer.follow(engine.events.subscribe()))
        await engine.run()
        await follower

    run(scenario())
    text = out.getvalue()
    assert "\nYou: @Ava hello" in text
    assert "\nAva → You: Ava answers" in text
    assert "\nAva: @Ben really?" in text
    assert "\nBen → Ava: Ben answers" in text


def test_lines_typed_in_the_terminal_are_posted():
    state = make_state(names=("Ava", "Ben"))
    engine, _ = make_engine(state)

    async def scenario():
        thread = listen_to_terminal(
            engine, asyncio.get_running_loop(), io.StringIO("@Ben hi there\n\n   \n")
        )
        thread.join(timeout=5)
        await asyncio.sleep(0)  # run the callbacks the thread scheduled
        await engine.run()

    run(scenario())
    users = [m.text for m in state.messages if m.speaker_kind is SpeakerKind.USER]
    assert users == ["@Ben hi there"]
    assert spoken(state)[:2] == [("User", None), ("Ben", "User")]


def test_default_script_still_has_no_mentions():
    state = make_state()
    engine, _ = make_engine(state, default_script)
    run(engine.run())
    assert not any(m.reply_to for m in state.messages)
