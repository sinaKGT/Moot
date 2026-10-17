"""Tests for personas, the moderator seat and discussion rules."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from moot.core import (
    GLOBAL_RULES,
    AIModerator,
    HumanModerator,
    ModelRef,
    Moderator,
    Persona,
    RulePreset,
    SessionRules,
    Traits,
    starter_personas,
)


def test_persona_defaults_and_unique_ids():
    first, second = Persona(name="Ava"), Persona(name="Ben")
    assert first.id != second.id
    assert first.id.startswith("agt_")
    assert first.traits == Traits()
    assert first.model is None


def test_persona_name_is_trimmed():
    assert Persona(name="  Ava  ").name == "Ava"


@pytest.mark.parametrize("bad_name", ["", "   ", "@Ava", "Line\nbreak", "x" * 41])
def test_persona_rejects_bad_names(bad_name):
    with pytest.raises(ValidationError):
        Persona(name=bad_name)


def test_persona_rejects_bad_colour_and_traits():
    with pytest.raises(ValidationError):
        Persona(name="Ava", color="red")
    with pytest.raises(ValidationError):
        Persona(name="Ava", traits=Traits(persuadability=1.5))


def test_persona_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Persona(name="Ava", favourite_colour="blue")


def test_model_ref_temperature_range():
    assert ModelRef(provider="ollama", model="llama3", temperature=0.7).temperature == 0.7
    with pytest.raises(ValidationError):
        ModelRef(provider="ollama", model="llama3", temperature=3)


def test_moderator_seat_is_ai_or_human():
    adapter = TypeAdapter(Moderator)
    assert isinstance(adapter.validate_python({"kind": "ai"}), AIModerator)
    human = adapter.validate_python({"kind": "human", "display_name": "Sina", "role": "Judge"})
    assert isinstance(human, HumanModerator)
    assert human.role == "Judge"
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "robot"})


def test_rules_combine_global_presets_custom_and_length():
    rules = SessionRules(
        presets=[RulePreset.SHORT_ANSWERS, RulePreset.SHORT_ANSWERS, RulePreset.FORMAL_TONE],
        custom=["  Answers must be logical.  ", "   "],
        max_words_per_turn=80,
    )
    assert rules.presets == [RulePreset.SHORT_ANSWERS, RulePreset.FORMAL_TONE]
    assert rules.custom == ["Answers must be logical."]
    all_rules = rules.all_rules()
    assert all_rules[: len(GLOBAL_RULES)] == list(GLOBAL_RULES)
    assert "Keep answers short and to the point." in all_rules
    assert "Answers must be logical." in all_rules
    assert all_rules[-1] == "Use at most 80 words per turn."


def test_global_rules_always_apply():
    assert SessionRules().all_rules() == list(GLOBAL_RULES)


def test_starter_personas_are_fresh_copies():
    first, second = starter_personas(), starter_personas()
    names = [p.name for p in first]
    assert names == ["Skeptic", "Pragmatist", "Domain Expert", "Devil's Advocate", "Innovator"]
    assert len({p.id for p in first + second}) == 10
    first[0].traits.persuadability = 0.9
    assert second[0].traits.persuadability == 0.4
