"""Tests for polls: one option per agent, no self-votes, real-poll style results."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moot.core import Persona, Poll, PollError, PollKind, PollSpec, PollStatus, winner_poll_spec


@pytest.fixture
def agents():
    return [Persona(name="Ava"), Persona(name="Ben"), Persona(name="Cleo")]


def test_choice_spec_needs_two_different_options():
    with pytest.raises(ValidationError):
        PollSpec(question="Best plan?", options=["A"])
    with pytest.raises(ValidationError):
        PollSpec(question="Best plan?", options=["Plan A", "plan a"])
    assert PollSpec(question="Best plan?", options=[" A ", "", "B"]).options == ["A", "B"]


def test_winner_spec_must_not_list_options():
    with pytest.raises(ValidationError):
        PollSpec(question="Who won?", kind=PollKind.WINNER, options=["Ava", "Ben"])


def test_winner_poll_uses_agents_as_options(agents):
    poll = Poll.open(winner_poll_spec(), agents, opened_by="moderator", round_index=4)
    assert [o.label for o in poll.options] == ["Ava", "Ben", "Cleo"]
    assert [o.agent_id for o in poll.options] == [a.id for a in agents]
    assert poll.eligible_voters == [a.id for a in agents]
    assert poll.status is PollStatus.OPEN
    assert poll.round_index == 4


def test_agents_cannot_vote_for_themselves(agents):
    ava = agents[0]
    poll = Poll.open(winner_poll_spec(), agents, opened_by="moderator")
    assert [o.label for o in poll.allowed_options(ava.id)] == ["Ben", "Cleo"]
    with pytest.raises(PollError, match="themselves"):
        poll.cast_vote(ava.id, f"opt_{ava.id}")


def test_one_vote_per_agent_and_valid_options_only(agents):
    ava, ben, _ = agents
    poll = Poll.open(PollSpec(question="Plan?", options=["A", "B"]), agents, opened_by="user")
    poll.cast_vote(ava.id, "opt_0")
    with pytest.raises(PollError, match="already voted"):
        poll.cast_vote(ava.id, "opt_1")
    with pytest.raises(PollError, match="unknown option"):
        poll.cast_vote(ben.id, "opt_9")
    with pytest.raises(PollError, match="cannot vote"):
        poll.cast_vote("agt_stranger", "opt_0")


def test_closed_poll_rejects_votes(agents):
    poll = Poll.open(PollSpec(question="Plan?", options=["A", "B"]), agents, opened_by="user")
    poll.close()
    with pytest.raises(PollError, match="closed"):
        poll.cast_vote(agents[0].id, "opt_0")


def test_results_like_a_real_poll(agents):
    ava, ben, cleo = agents
    poll = Poll.open(winner_poll_spec(), agents, opened_by="moderator")
    assert not poll.complete
    poll.cast_vote(ava.id, f"opt_{ben.id}")
    poll.cast_vote(cleo.id, f"opt_{ben.id}")
    poll.cast_vote(ben.id, f"opt_{ava.id}")
    assert poll.complete

    results = {r.label: r for r in poll.results()}
    assert [r.label for r in poll.results()] == ["Ava", "Ben", "Cleo"]
    assert (results["Ben"].votes, results["Ben"].percent) == (2, 66.7)
    assert set(results["Ben"].voters) == {ava.id, cleo.id}
    assert (results["Ava"].votes, results["Ava"].percent) == (1, 33.3)
    assert (results["Cleo"].votes, results["Cleo"].percent) == (0, 0.0)


def test_results_with_no_votes(agents):
    poll = Poll.open(winner_poll_spec(), agents, opened_by="moderator")
    assert all(r.votes == 0 and r.percent == 0.0 for r in poll.results())
