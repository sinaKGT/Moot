"""Polls: agents vote by picking one option, with no text. Results look like a real poll."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from moot.core.base import MootModel, new_id
from moot.core.persona import Persona

WINNER_QUESTION = "Who made the strongest case?"


class PollError(ValueError):
    """An invalid poll action, such as voting twice or voting for yourself."""


class PollKind(StrEnum):
    WINNER = "winner"  # options are the agents; nobody can vote for themselves
    CHOICE = "choice"  # options are written by the moderator


class PollStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class PollSpec(MootModel):
    """What to ask. Becomes a `Poll` when the moderator opens it."""

    question: str = Field(min_length=1)
    kind: PollKind = PollKind.CHOICE
    options: list[str] = Field(default_factory=list)

    @field_validator("options")
    @classmethod
    def _clean_options(cls, value: list[str]) -> list[str]:
        return [option.strip() for option in value if option.strip()]

    @model_validator(mode="after")
    def _check_options(self) -> PollSpec:
        if self.kind is PollKind.WINNER and self.options:
            raise ValueError("a winner poll uses the agents as options; don't list options")
        if self.kind is PollKind.CHOICE:
            if len(self.options) < 2:
                raise ValueError("a choice poll needs at least two options")
            if len({option.casefold() for option in self.options}) != len(self.options):
                raise ValueError("poll options must be different from each other")
        return self


def winner_poll_spec() -> PollSpec:
    return PollSpec(question=WINNER_QUESTION, kind=PollKind.WINNER)


class PollOption(MootModel):
    id: str
    label: str
    agent_id: str | None = None


class PollResult(MootModel):
    option_id: str
    label: str
    votes: int
    percent: float
    voters: list[str]


class Poll(MootModel):
    id: str = Field(default_factory=lambda: new_id("pol"))
    question: str
    kind: PollKind
    options: list[PollOption] = Field(min_length=2)
    eligible_voters: list[str] = Field(min_length=1)
    votes: dict[str, str] = Field(default_factory=dict, description="voter id -> option id")
    status: PollStatus = PollStatus.OPEN
    opened_by: str
    round_index: int | None = None
    previous_id: str | None = Field(
        default=None, description="an earlier poll with the same question, to compare with"
    )

    @classmethod
    def open(
        cls,
        spec: PollSpec,
        agents: Sequence[Persona],
        *,
        opened_by: str,
        round_index: int | None = None,
        previous_id: str | None = None,
    ) -> Poll:
        if spec.kind is PollKind.WINNER:
            options = [PollOption(id=f"opt_{a.id}", label=a.name, agent_id=a.id) for a in agents]
        else:
            options = [PollOption(id=f"opt_{i}", label=text) for i, text in enumerate(spec.options)]
        return cls(
            question=spec.question,
            kind=spec.kind,
            options=options,
            eligible_voters=[agent.id for agent in agents],
            opened_by=opened_by,
            round_index=round_index,
            previous_id=previous_id,
        )

    def allowed_options(self, voter_id: str) -> list[PollOption]:
        """The options this voter may choose (everyone except themselves in a winner poll)."""
        return [option for option in self.options if option.agent_id != voter_id]

    def cast_vote(self, voter_id: str, option_id: str) -> None:
        if self.status is PollStatus.CLOSED:
            raise PollError("the poll is closed")
        if voter_id not in self.eligible_voters:
            raise PollError(f"{voter_id} cannot vote in this poll")
        if voter_id in self.votes:
            raise PollError(f"{voter_id} has already voted")
        option = next((o for o in self.options if o.id == option_id), None)
        if option is None:
            raise PollError(f"unknown option {option_id}")
        if option.agent_id == voter_id:
            raise PollError("agents cannot vote for themselves")
        self.votes[voter_id] = option_id

    @property
    def complete(self) -> bool:
        return all(voter in self.votes for voter in self.eligible_voters)

    def close(self) -> None:
        self.status = PollStatus.CLOSED

    def results(self) -> list[PollResult]:
        """Counts and percentages per option, in the options' original order."""
        total = len(self.votes)
        results = []
        for option in self.options:
            voters = [voter for voter, chosen in self.votes.items() if chosen == option.id]
            results.append(
                PollResult(
                    option_id=option.id,
                    label=option.label,
                    votes=len(voters),
                    percent=round(100 * len(voters) / total, 1) if total else 0.0,
                    voters=voters,
                )
            )
        return results
