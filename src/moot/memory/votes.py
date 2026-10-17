"""Poll results in words, for briefings, the moderator and the console.

Votes are public: each result names who chose each option.
"""

from __future__ import annotations

from moot.core import Persona, Poll, PollStatus, SessionState
from moot.memory.alignment import names_of

MAX_POLLS_SHOWN = 2


def _counts(state: SessionState, poll: Poll, *, with_voters: bool) -> str:
    parts = []
    for result in poll.results():
        votes = f"{result.votes} vote{'s' if result.votes != 1 else ''}"
        voters = f" ({names_of(state, result.voters)})" if with_voters and result.voters else ""
        parts.append(f"{result.label} {votes}{voters}")
    return ", ".join(parts)


def previous_poll(state: SessionState, poll: Poll) -> Poll | None:
    return next((p for p in state.polls if p.id == poll.previous_id), None)


def poll_line(state: SessionState, poll: Poll, agent: Persona | None = None) -> str:
    """'"Who made the strongest case?" (round 4): Ava 2 votes (Ben, Cleo), ...'"""
    when = f" (round {poll.round_index + 1})" if poll.round_index is not None else ""
    line = f'"{poll.question}"{when}: {_counts(state, poll, with_voters=True)}.'
    earlier = previous_poll(state, poll)
    if earlier is not None:
        line += f" The same poll earlier: {_counts(state, earlier, with_voters=False)}."
    if agent is not None and agent.id in poll.votes:
        chosen = next(o.label for o in poll.options if o.id == poll.votes[agent.id])
        line += f" You voted {chosen}."
    return line


def poll_lines(
    state: SessionState, agent: Persona | None = None, *, limit: int | None = MAX_POLLS_SHOWN
) -> list[str]:
    """The most recent closed polls, newest last."""
    closed = [p for p in state.polls if p.status is PollStatus.CLOSED]
    if limit is not None:
        closed = closed[-limit:]
    return [f"- {poll_line(state, poll, agent)}" for poll in closed]
