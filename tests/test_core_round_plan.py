"""Tests for rounds, round plans and the format templates."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moot.core import (
    TEMPLATES,
    PollKind,
    PollSpec,
    Round,
    RoundPlan,
    RoundType,
    SpeakingOrder,
    classic_debate,
)


def test_round_defaults_come_from_its_type():
    opening = Round(type=RoundType.OPENING)
    assert opening.name == "Opening statements"
    assert opening.instruction.startswith("Give your opening position")
    assert opening.blind is True
    assert opening.mentions_allowed is False
    assert opening.speaking_order is SpeakingOrder.FIXED

    questions = Round(type=RoundType.QA)
    assert questions.mentions_allowed is True
    assert questions.blind is False


def test_explicit_values_override_defaults():
    custom = Round(
        type=RoundType.OPENING,
        name="Ideas",
        instruction="Pitch one idea.",
        blind=False,
        mentions_allowed=True,
        turns_per_agent=2,
    )
    assert (custom.name, custom.instruction) == ("Ideas", "Pitch one idea.")
    assert custom.blind is False
    assert custom.mentions_allowed is True
    assert custom.turns_per_agent == 2


def test_vote_round_gets_a_winner_poll_by_default():
    vote = Round(type=RoundType.VOTE)
    assert vote.poll is not None
    assert vote.poll.kind is PollKind.WINNER


def test_only_vote_rounds_can_have_a_poll():
    poll = PollSpec(question="Best plan?", options=["A", "B"])
    assert Round(type=RoundType.VOTE, poll=poll).poll == poll
    with pytest.raises(ValidationError, match="only vote rounds"):
        Round(type=RoundType.OPENING, poll=poll)


def test_round_limits():
    with pytest.raises(ValidationError):
        Round(type=RoundType.FREE, turns_per_agent=0)
    with pytest.raises(ValidationError):
        Round(type=RoundType.FREE, max_words=5)
    with pytest.raises(ValidationError):
        Round(type="speech")


def test_plan_needs_rounds_with_unique_ids():
    with pytest.raises(ValidationError):
        RoundPlan(rounds=[])
    duplicate = Round(type=RoundType.OPENING)
    with pytest.raises(ValidationError, match="unique id"):
        RoundPlan(rounds=[duplicate, duplicate])


def test_plan_editing():
    plan = classic_debate()
    extra = Round(type=RoundType.QA)
    plan.add(extra, index=1)
    assert plan.rounds[1] is extra
    plan.move(extra.id, len(plan) - 1)
    assert plan.rounds[-1] is extra
    plan.remove(extra.id)
    assert extra not in plan.rounds
    with pytest.raises(ValueError):
        plan.add(plan.rounds[0])
    with pytest.raises(ValueError):
        plan.remove("rnd_missing")


def test_cannot_remove_the_last_round():
    plan = RoundPlan(rounds=[Round(type=RoundType.OPENING)])
    with pytest.raises(ValueError, match="at least one"):
        plan.remove(plan.rounds[0].id)


def test_classic_debate_shape():
    types = [r.type for r in classic_debate().rounds]
    assert types == [
        RoundType.OPENING,
        RoundType.CHALLENGE,
        RoundType.REBUTTAL,
        RoundType.CLOSING,
        RoundType.VOTE,
    ]


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_templates_build_valid_plans_that_survive_json(template_id):
    _label, factory = TEMPLATES[template_id]
    plan = factory()
    assert RoundPlan.model_validate_json(plan.model_dump_json()) == plan
