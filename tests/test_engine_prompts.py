"""Tests for prompt building and name-prefix cleanup."""

from __future__ import annotations

import pytest

from moot.core import (
    GLOBAL_RULES,
    ChatMessage,
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
    Traits,
)
from moot.engine.prompts import (
    build_agent_messages,
    describe_traits,
    persona_prompt,
    speaker_name,
    turn_prompt,
    visible_messages,
)
from moot.engine.text import NamePrefixStripper

MODEL = ModelRef(provider="fake", model="m")


def make_config(**overrides) -> SessionConfig:
    settings = {
        "topic": "Should cities ban cars?",
        "background": "The city council votes next month.",
        "agents": [
            Persona(
                name="Ava",
                role="Advocate",
                viewpoint="Cars should be banned.",
                motivation="Win the debate.",
                expertise="Urban planning.",
                thinking_style="Constructive.",
            ),
            Persona(name="Ben", role="Critic"),
            Persona(name="Cleo"),
        ],
        "rules": SessionRules(presets=[RulePreset.SHORT_ANSWERS], custom=["Be logical."]),
        "round_plan": RoundPlan(
            rounds=[Round(type=RoundType.OPENING), Round(type=RoundType.FREE, turns_per_agent=2)]
        ),
        "default_model": MODEL,
    }
    settings.update(overrides)
    return SessionConfig(**settings)


def test_persona_prompt_contains_identity_setting_and_rules():
    config = make_config()
    prompt = persona_prompt(config, config.agents[0])

    assert prompt.startswith("You are Ava (Advocate), a participant in a moderated discussion.")
    for expected in (
        "Your viewpoint: Cars should be banned.",
        "What you want from this discussion: Win the debate.",
        "Your expertise: Urban planning.",
        "How you argue: Constructive.",
        "Topic: Should cities ban cars?",
        "Background: The city council votes next month.",
        "Moderator: Moderator",
        "Other participants: Ben (Critic), Cleo",
        "- Keep answers short and to the point.",
        "- Be logical.",
        "How to take part:",
        "don't just ask for evidence",
        "Speak only as Ava.",
    ):
        assert expected in prompt
    for rule in GLOBAL_RULES:
        assert f"- {rule}" in prompt


def test_prompt_override_replaces_the_persona_but_keeps_the_rules():
    config = make_config()
    config.agents[1].prompt_override = "You are Ben, a grumpy taxi driver."
    prompt = persona_prompt(config, config.agents[1])

    assert prompt.startswith("You are Ben, a grumpy taxi driver.")
    assert "a participant in a moderated discussion" not in prompt
    assert f"- {GLOBAL_RULES[0]}" in prompt
    assert "Topic: Should cities ban cars?" in prompt


def test_human_moderator_is_named_with_role():
    config = make_config(
        moderator=HumanModerator(display_name="Sina", role="Judge"), graph_builder_model=MODEL
    )
    prompt = persona_prompt(config, config.agents[0])
    assert "Moderator: Sina (Judge)" in prompt
    assert "Sina is a person moderating as Judge. Address them as Sina" in prompt


@pytest.mark.parametrize(
    ("traits", "expected"),
    [
        (Traits(persuadability=0.1), "hold your position firmly"),
        (Traits(persuadability=0.9), "open to persuasion"),
        (Traits(verbosity=0.1), "Keep your answers brief."),
        (Traits(verbosity=0.9), "detailed"),
        (Traits(alliance_openness=0.1), "stand alone"),
        (Traits(alliance_openness=0.9), "look for allies"),
    ],
)
def test_traits_become_instructions(traits, expected):
    assert any(expected in line for line in describe_traits(traits))


def test_turn_prompt_describes_the_round():
    state = SessionState.new(make_config(rules=SessionRules(max_words_per_turn=100)))
    ava = state.config.agents[0]
    opening, free = state.config.round_plan.rounds

    first = turn_prompt(state, ava, 0, opening, 0)
    assert "Nobody has spoken yet." in first
    assert "Round 1 of 2: Opening statements" in first
    assert "This round is blind" in first
    assert "Use at most 100 words (not counting your private notes)." in first
    assert "It is your turn, Ava." in first
    assert "<notes>" in first and first.rstrip().endswith(
        "Use the participants' names exactly as written."
    )

    free.max_words = 40
    later = turn_prompt(state, ava, 1, free, 1)
    assert "This is your turn 2 of 2 in this round." in later
    assert "Use at most 40 words" in later
    assert "blind" not in later


def test_visibility_and_speaker_names():
    config = make_config()
    state = SessionState.new(config)
    ava, ben, _ = config.agents
    opening = config.round_plan.rounds[0]
    state.messages += [
        ChatMessage(speaker_id=ben.id, speaker_kind=SpeakerKind.AGENT, text="B", round_index=0),
        ChatMessage(speaker_id="system", speaker_kind=SpeakerKind.SYSTEM, text="note"),
        ChatMessage(speaker_id="moderator", speaker_kind=SpeakerKind.MODERATOR, text="M"),
        ChatMessage(speaker_id="user", speaker_kind=SpeakerKind.USER, text="U"),
    ]

    assert [m.text for m in visible_messages(state, ava, 0, opening)] == ["M", "U"]
    assert [m.text for m in visible_messages(state, ava, 1, opening)] == ["B", "M", "U"]
    assert [speaker_name(config, m) for m in state.messages] == [
        "Ben",
        "System",
        "Moderator",
        "User",
    ]


def test_agent_messages_are_system_then_user():
    state = SessionState.new(make_config())
    messages = build_agent_messages(
        state, state.config.agents[0], 0, state.config.round_plan.rounds[0], 0
    )
    assert [m.role for m in messages] == ["system", "user"]


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        (["Ava: Hello"], "Hello"),
        (["Av", "a: Hel", "lo"], "Hello"),
        (["**Ava:** Hi"], "Hi"),
        (["ava: hi"], "hi"),
        (["  Ava:  spaced"], "spaced"),
        (["Av", "oid this"], "Avoid this"),
        (["Hello there"], "Hello there"),
        (["Ava"], "Ava"),
    ],
)
def test_name_prefix_stripper(chunks, expected):
    stripper = NamePrefixStripper("Ava")
    output = "".join(stripper.feed(chunk) for chunk in chunks) + stripper.flush()
    assert output == expected


def test_stripper_streams_normal_text_immediately():
    stripper = NamePrefixStripper("Ava")
    assert stripper.feed("Cars ") == "Cars "
    assert stripper.feed("pollute.") == "pollute."
