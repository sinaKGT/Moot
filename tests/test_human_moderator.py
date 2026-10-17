"""Tests for human-moderator mode: round gates, giving the floor, ending rounds,
reminders, @all, and the console and terminal commands."""

from __future__ import annotations

import asyncio
import importlib.util
import io
from pathlib import Path

import pytest
from fakes import FakeProvider, run_moderated, speaker_of

from moot.console import ConsoleRenderer
from moot.core import (
    MODERATOR_ID,
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
    SpeakerKind,
    SpeakingOrder,
)
from moot.core.events import (
    ModeratorDecision,
    RoundEnded,
    RuleViolation,
    SessionEnded,
    TurnCompleted,
    WaitingForModerator,
)
from moot.demo import MODERATOR_HELP, demo_config, run_in_console, terminal_command
from moot.engine import DiscussionEngine, ProviderPool
from moot.engine.mentions import find_mentions

MODEL = ModelRef(provider="fake", model="fake-model")
SINA = HumanModerator(display_name="Sina", role="Judge")


def make_state(rounds=None, names=("Ava", "Ben", "Cleo"), human=True, **config) -> SessionState:
    settings = {
        "topic": "Should cities ban cars?",
        "agents": [Persona(name=name) for name in names],
        "round_plan": RoundPlan(
            rounds=rounds or [Round(type=RoundType.OPENING), Round(type=RoundType.CLOSING)]
        ),
        "rules": SessionRules(presets=[]),
        "default_model": MODEL,
    }
    if human:
        settings |= {"moderator": SINA, "graph_builder_model": MODEL}
    settings.update(config)
    return SessionState.new(SessionConfig(**settings))


def make_engine(state, script=None, **kwargs):
    provider = FakeProvider(script)
    kwargs.setdefault("build_graph", False)
    pool = ProviderPool(factory=lambda preset_id: provider)
    return DiscussionEngine(state, providers=pool, **kwargs), provider


def spoken(state, round_index=None):
    return [
        state.config.agent(m.speaker_id).name
        for m in state.messages
        if m.speaker_kind is SpeakerKind.AGENT
        and (round_index is None or m.round_index == round_index)
    ]


def waits(engine):
    return [
        (e.waiting_for, e.round_index)
        for e in engine.events.history
        if isinstance(e, WaitingForModerator)
    ]


def moderated_round():
    return Round(type=RoundType.CHALLENGE, speaking_order=SpeakingOrder.MODERATOR)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ round gates


def test_the_engine_waits_for_the_moderator_before_each_round_and_at_the_end():
    state = make_state()
    engine, _ = make_engine(state)
    seen = []

    def respond(event):
        seen.append((event.waiting_for, event.round_index, len(spoken(state)), engine.waiting_for))
        return False  # let the stand-in moderator press "next"

    run(run_moderated(engine, respond))

    assert seen == [
        ("start_round", 0, 0, "start_round"),
        ("start_round", 1, 3, "start_round"),
        ("finish", 1, 6, "finish"),
    ]
    assert engine.waiting_for is None
    assert engine.events.history[-1].reason == "completed"


def test_the_moderator_can_speak_before_starting_and_agents_see_it():
    state = make_state()
    engine, provider = make_engine(state)

    def respond(event):
        if event.waiting_for == "start_round" and event.round_index == 0 and not state.messages:
            engine.say("Welcome. Keep it short, please.")
            engine.next_round()
            return True
        return False

    run(run_moderated(engine, respond))

    first = state.messages[0]
    assert (first.speaker_id, first.speaker_kind) == (MODERATOR_ID, SpeakerKind.MODERATOR)
    ava_opening = provider.requests[0].messages[-1].content
    assert "Sina: Welcome. Keep it short, please." in ava_opening  # visible in a blind round
    assert "Sina is a person moderating as Judge" in provider.requests[0].messages[0].content


def test_no_ai_moderator_turns_or_automatic_reminders():
    state = make_state(rules=SessionRules(presets=[RulePreset.ANSWER_FIRST]))
    engine, provider = make_engine(state)
    run(run_moderated(engine))

    assert not any(
        "the moderator of a discussion" in r.messages[0].content for r in provider.requests
    )
    assert [m for m in state.messages if m.speaker_kind is SpeakerKind.MODERATOR] == []
    assert len([e for e in engine.events.history if isinstance(e, RuleViolation)]) == 6
    assert not any("About your last answer" in r.messages[-1].content for r in provider.requests)


# ------------------------------------------------------------------ the floor


def test_the_moderator_chooses_every_speaker_in_a_moderated_round():
    state = make_state(rounds=[moderated_round()])
    engine, _ = make_engine(state)
    choices = ["Ben", "Ben", "Ava"]

    def respond(event):
        if event.waiting_for == "next_speaker":
            if choices:
                engine.give_floor(choices.pop(0))
            else:
                engine.end_round()
            return True
        return False

    run(run_moderated(engine, respond))

    assert spoken(state) == ["Ben", "Ben", "Ava"]  # turns are the moderator's call
    decisions = [e for e in engine.events.history if isinstance(e, ModeratorDecision)]
    assert [state.config.agent(e.next_speaker_id).name for e in decisions] == ["Ben", "Ben", "Ava"]
    assert all(e.reason == "" for e in decisions)
    assert waits(engine).count(("next_speaker", 0)) == 4


def test_giving_the_floor_to_nobody_lets_the_engine_choose_fairly():
    state = make_state(rounds=[moderated_round()])
    engine, _ = make_engine(state)
    grants = [None, None, None]

    def respond(event):
        if event.waiting_for == "next_speaker":
            if grants:
                engine.give_floor(grants.pop())
            else:
                engine.next_round()
            return True
        return False

    run(run_moderated(engine, respond))
    assert sorted(spoken(state)) == ["Ava", "Ben", "Cleo"]  # everyone once
    assert all(e.reason for e in engine.events.history if isinstance(e, ModeratorDecision))


def test_the_floor_in_an_automatic_round_means_an_extra_turn_next():
    state = make_state(rounds=[Round(type=RoundType.CLOSING)])
    engine, _ = make_engine(state)

    async def scenario():
        async def control():
            async for event in engine.events.subscribe():
                if isinstance(event, TurnCompleted) and spoken(state) == ["Ava"]:
                    engine.give_floor("Ava")
                    return

        task = asyncio.create_task(control())
        await run_moderated(engine)
        await task

    run(scenario())
    assert spoken(state) == ["Ava", "Ava", "Ben", "Cleo"]


def test_give_floor_needs_a_participant():
    engine, _ = make_engine(make_state())
    with pytest.raises(ValueError, match="Zed"):
        engine.give_floor("Zed")


# ------------------------------------------------------------------ moving on


def end_after_first_turn(engine, state, how):
    async def scenario():
        async def control():
            async for event in engine.events.subscribe():
                if isinstance(event, TurnCompleted) and len(spoken(state)) == 1:
                    getattr(engine, how)()
                    return

        task = asyncio.create_task(control())
        await run_moderated(engine)
        await task

    run(scenario())


def test_end_round_cuts_the_round_short_and_waits():
    state = make_state()
    engine, _ = make_engine(state)
    end_after_first_turn(engine, state, "end_round")
    assert spoken(state, 0) == ["Ava"]
    assert spoken(state, 1) == ["Ava", "Ben", "Cleo"]
    assert ("start_round", 1) in waits(engine)
    assert [e.round_index for e in engine.events.history if isinstance(e, RoundEnded)] == [0, 1]


def test_next_round_moves_straight_on():
    state = make_state()
    engine, _ = make_engine(state)
    end_after_first_turn(engine, state, "next_round")
    assert spoken(state, 0) == ["Ava"]
    assert spoken(state, 1) == ["Ava", "Ben", "Cleo"]
    assert ("start_round", 1) not in waits(engine)


def test_finish_ends_the_discussion_as_completed():
    state = make_state()
    engine, _ = make_engine(state)
    end_after_first_turn(engine, state, "finish")
    assert spoken(state) == ["Ava"]
    assert engine.events.history[-1].reason == "completed"


def test_finish_with_the_ai_moderator_still_gets_a_report():
    state = make_state(human=False)
    engine, _ = make_engine(state)
    end_after_first_turn(engine, state, "finish")
    assert spoken(state) == ["Ava"]
    assert state.report == "Answer: undecided."
    assert engine.events.history[-1].reason == "completed"


def test_stop_while_waiting():
    state = make_state()
    engine, _ = make_engine(state)

    def respond(event):
        engine.stop()
        return True

    run(run_moderated(engine, respond))
    assert spoken(state) == []
    assert isinstance(engine.events.history[-1], SessionEnded)
    assert engine.events.history[-1].reason == "stopped"


# ------------------------------------------------------------------ reminders and @all


def test_reminders_are_public_and_reach_the_next_prompt():
    state = make_state()
    engine, provider = make_engine(state)

    def respond(event):
        if (event.waiting_for, event.round_index) == ("start_round", 1):
            engine.remind("Ben", "Keep it under 50 words.")
            engine.next_round()
            return True
        return False

    run(run_moderated(engine, respond))
    line = next(m for m in state.messages if m.speaker_kind is SpeakerKind.MODERATOR)
    assert line.text == "Ben, a reminder: Keep it under 50 words."
    ben_prompts = [r.messages[-1].content for r in provider.requests if speaker_of(r) == "Ben"]
    assert "About your last answer:\n- Keep it under 50 words." in ben_prompts[1]
    assert "Keep it under 50 words." not in ben_prompts[0]


def test_at_all_mentions_every_agent():
    agents = [Persona(name="Ava"), Persona(name="Ben"), Persona(name="Cleo")]
    ava, ben, cleo = (a.id for a in agents)
    assert find_mentions("@all, one word each", agents) == [ava, ben, cleo]
    assert find_mentions("@Cleo first, then @everyone", agents) == [cleo, ava, ben]
    assert find_mentions("@all of you", agents, exclude=ben) == [ava, cleo]
    assert find_mentions("call@all.com", agents) == []


def test_the_moderator_asks_everyone_at_once():
    state = make_state()
    engine, _ = make_engine(state)
    engine.say("@all One word: yes or no?")
    run(run_moderated(engine))
    question = state.messages[0]
    replies = [m for m in state.messages if m.reply_to == question.id]
    assert [state.config.agent(m.speaker_id).name for m in replies] == ["Ava", "Ben", "Cleo"]


# ------------------------------------------------------------------ console and terminal


def test_console_tells_the_moderator_what_they_can_do():
    state = make_state(rounds=[moderated_round()])
    engine, _ = make_engine(state)
    out = io.StringIO()
    renderer = ConsoleRenderer(state.config, out=out, color=False, state=state)
    asked = []

    def respond(event):
        if event.waiting_for == "next_speaker" and not asked:
            asked.append(event)
            engine.give_floor("Ben")
            return True
        return False

    async def scenario():
        follower = asyncio.create_task(renderer.follow(engine.events.subscribe()))
        await run_moderated(engine, respond)
        await follower

    run(scenario())
    text = out.getvalue()
    assert "Moderator: Sina (Judge, you)\n" in text
    assert "» Your move, Sina: /next starts round 1 (Challenge)." in text
    assert "» Who speaks next? /floor Name gives someone the floor" in text
    assert "· Sina gives the floor to Ben\n" in text
    assert "» The round plan is done." in text


def test_moderator_commands_in_the_terminal():
    state = make_state(rounds=[moderated_round(), Round(type=RoundType.CLOSING)])
    engine, _ = make_engine(state)
    typed = iter(["/floor Ben", "/remind Ben  keep it short", "/floor", "/end"])

    def respond(event):
        if event.waiting_for == "next_speaker":
            line = next(typed, "/next")
            terminal_command(engine, line)
            if line.startswith("/remind"):
                terminal_command(engine, next(typed))
            return True
        return False

    run(run_moderated(engine, respond))
    assert spoken(state, 0)[0] == "Ben"
    assert len(spoken(state, 0)) == 2
    assert any(m.text == "Ben, a reminder: keep it short" for m in state.messages)
    with pytest.raises(ValueError, match="participant's name"):
        terminal_command(engine, "/remind Nobody hush")
    assert "/floor Name" in terminal_command(engine, "/dance")


def test_finish_command():
    state = make_state()
    engine, _ = make_engine(state)

    def respond(event):
        terminal_command(engine, "/finish")
        return True

    run(run_moderated(engine, respond))
    assert spoken(state) == []
    assert engine.events.history[-1].reason == "completed"


def test_the_console_needs_a_terminal_when_you_moderate():
    state = make_state()
    with pytest.raises(ValueError, match="terminal"):
        run(run_in_console(state, out=io.StringIO()))


def test_demo_config_with_you_as_moderator():
    config = demo_config("Topic?", MODEL, moderator=SINA)
    assert config.moderator == SINA
    assert config.graph_model() == MODEL
    challenge = config.round_plan.rounds[1]
    assert challenge.speaking_order is SpeakingOrder.MODERATOR
    assert MODERATOR_HELP.startswith("As the moderator:")


def load_watch_debate():
    path = Path(__file__).resolve().parent.parent / "scripts" / "watch_debate.py"
    spec = importlib.util.spec_from_file_location("watch_debate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_watch_debate_asks_who_moderates(monkeypatch, capsys):
    module = load_watch_debate()
    answers = iter(["2", "ava", "Sina", "Judge"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    moderator = module.ask_moderator(["Ava", "Ben", "Cleo"])
    assert moderator == SINA
    assert "isn't Ava, Ben, Cleo" in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert module.ask_moderator(["Ava"]) is None


def test_next_in_the_last_round_ends_it_and_waits_for_the_finish():
    state = make_state(rounds=[moderated_round()])
    engine, _ = make_engine(state)
    run(run_moderated(engine))  # the stand-in presses "next" whenever it's asked
    assert waits(engine) == [("start_round", 0), ("next_speaker", 0), ("finish", 0)]
    assert engine.events.history[-1].reason == "completed"
