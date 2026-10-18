"""Tests for decisions: options, the agents' choices, the vote among the options and the
decision-style conclusion."""

from __future__ import annotations

import asyncio

import pytest
from fakes import FakeProvider, default_script, is_vote_request, moderator_reply, speaker_of
from pydantic import ValidationError

from moot.core import (
    TEMPLATES,
    ModelRef,
    Persona,
    RulePreset,
    SessionConfig,
    SessionRules,
    SessionState,
)
from moot.core.events import NotebookUpdated
from moot.engine import DiscussionEngine, ProviderPool
from moot.engine.moderator import AUDIENCE_PREFIX, REPORT_TASK, ModeratorBrain
from moot.engine.prompts import persona_prompt, turn_prompt
from moot.engine.rule_checks import check_rules
from moot.memory import match_option, notes_instructions

MODEL = ModelRef(provider="fake", model="fake-model")
OPTIONS = ["Cloud", "On-premise", "Hybrid"]


def make_config(**overrides) -> SessionConfig:
    settings = {
        "topic": "Where should the lab run its simulations?",
        "agents": [Persona(name="Ava"), Persona(name="Ben"), Persona(name="Cleo")],
        "round_plan": TEMPLATES["decision"][1](),
        "rules": SessionRules(presets=[RulePreset.ANSWER_FIRST]),
        "default_model": MODEL,
        "options": OPTIONS,
    }
    settings.update(overrides)
    return SessionConfig(**settings)


# ------------------------------------------------------------------ the config


def test_options_are_cleaned_and_make_it_a_decision():
    config = make_config(options=["  Cloud ", "cloud", "On-premise", ""])
    assert config.options == ["Cloud", "On-premise"]
    assert config.is_decision
    assert not make_config(options=[]).is_decision


@pytest.mark.parametrize("options", [["Only one"], ["x" * 121, "y"], [str(i) for i in range(9)]])
def test_bad_options_are_refused(options):
    with pytest.raises(ValidationError):
        make_config(options=options)


# ------------------------------------------------------------------ what agents are told


def test_agents_see_the_options_and_note_their_choice():
    config = make_config()
    state = SessionState.new(config)
    ava = config.agents[0]
    assert "This is a decision. The options: Cloud; On-premise; Hybrid." in persona_prompt(
        config, ava
    )
    prompt = turn_prompt(state, ava, 0, config.round_plan.rounds[0], 0)
    assert '"choice": the option you favour now' in prompt
    assert '"choice"' not in notes_instructions(make_config(options=[]))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hybrid", "Hybrid"),
        (" Cloud. ", "Cloud"),
        ("on-prem", "On-premise"),
        ("Rent a GPU box", "Rent a GPU box"),
    ],
)
def test_choices_match_the_listed_options(text, expected):
    assert match_option(text, OPTIONS) == expected


def test_no_yes_no_check_for_a_choice_between_options():
    config = make_config(topic="Should we use the cloud or our own servers?")
    current = config.round_plan.rounds[0]
    assert check_rules(config, current, "The cloud, because it scales.") == []
    yes_no = make_config(topic="Should we use the cloud?", options=[])
    assert check_rules(yes_no, current, "The cloud, because it scales.")


# ------------------------------------------------------------------ a whole decision

NOTES = {"Ava": "hybrid", "Ben": "On-premise", "Cleo": "Hybrid"}
VOTES = {"Ava": "Hybrid", "Ben": "Hybrid", "Cleo": "C"}


def script(request):
    if is_vote_request(request):
        return [f'{{"choice": "{VOTES[speaker_of(request)]}"}}']
    if REPORT_TASK in request.messages[-1].content:
        return ["Decision: Hybrid\nWhy: most of the panel favoured it.\nNext steps:\n- Price it"]
    reply = moderator_reply(request)
    if reply is not None:
        return reply
    speaker = speaker_of(request)
    if speaker in NOTES:
        choice = NOTES[speaker]
        notes = f'<notes>{{"verdict": "unsure", "confidence": 0.6, "choice": "{choice}"}}</notes>'
        return [*default_script(request), notes]
    return default_script(request)


def run_decision():
    state = SessionState.new(make_config())
    provider = FakeProvider(script)
    engine = DiscussionEngine(
        state, providers=ProviderPool(factory=lambda preset_id: provider), build_graph=False
    )
    asyncio.run(engine.run())
    return state, engine, provider


def test_a_decision_runs_from_options_to_a_decision():
    state, engine, provider = run_decision()
    ava, ben, cleo = state.config.agents

    choices = {
        e.agent_id: e.choice for e in engine.events.history if isinstance(e, NotebookUpdated)
    }
    assert choices == {ava.id: "Hybrid", ben.id: "On-premise", cleo.id: "Hybrid"}
    assert state.notebooks[ben.id].history[-1].choice == "On-premise"

    poll = state.polls[-1]
    assert poll.question == "Which option should we choose?"
    assert [o.label for o in poll.options] == OPTIONS
    labels = {o.id: o.label for o in poll.options}
    assert sorted(labels[v] for v in poll.votes.values()) == ["Hybrid", "Hybrid", "Hybrid"]

    assert state.report.startswith("Decision: Hybrid")
    assert state.audience_question is None

    moderator = [
        r.messages[-1].content
        for r in provider.requests
        if "the moderator of a discussion" in r.messages[0].content
    ]
    opening = moderator[0]
    assert (
        "options (Cloud; On-premise; Hybrid)" in opening
        and "criteria the decision should weigh" in opening
    )
    report = next(p for p in moderator if REPORT_TASK in p)
    assert "Decision: the option to choose" in report and "Next steps:" in report
    assert AUDIENCE_PREFIX not in report
    assert "round 1 Hybrid (60%)" in report  # positions over time are choices


def test_a_debate_still_ends_with_a_question_for_the_audience():
    config = make_config(options=[], round_plan=TEMPLATES["quick_take"][1]())
    report = ModeratorBrain().report_messages(SessionState.new(config))[-1].content
    assert AUDIENCE_PREFIX in report and "Decision:" not in report
