"""The round plan: the discussion format, defined round by round."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from moot.core.base import MootModel, new_id
from moot.core.polls import PollSpec, winner_poll_spec


class RoundType(StrEnum):
    OPENING = "opening"
    CHALLENGE = "challenge"
    QA = "qa"
    REBUTTAL = "rebuttal"
    FREE = "free"
    VOTE = "vote"
    CLOSING = "closing"


class SpeakingOrder(StrEnum):
    FIXED = "fixed"  # agents speak in panel order
    MODERATOR = "moderator"  # the moderator picks who speaks next
    FREE = "free"  # agents who want to speak raise a hand; the engine picks fairly


DEFAULT_NAMES: dict[RoundType, str] = {
    RoundType.OPENING: "Opening statements",
    RoundType.CHALLENGE: "Challenge",
    RoundType.QA: "Questions",
    RoundType.REBUTTAL: "Rebuttal",
    RoundType.FREE: "Free discussion",
    RoundType.VOTE: "Vote",
    RoundType.CLOSING: "Closing statements",
}

DEFAULT_INSTRUCTIONS: dict[RoundType, str] = {
    RoundType.OPENING: "Give your opening position on the topic.",
    RoundType.CHALLENGE: (
        "Challenge the strongest argument you disagree with, and name the participant who made it."
    ),
    RoundType.QA: (
        "Ask another participant a direct question using @Name, or answer questions put to you."
    ),
    RoundType.REBUTTAL: "Respond to the challenges made against your position.",
    RoundType.FREE: "Respond to whatever matters most in the discussion right now.",
    RoundType.VOTE: "Vote in the poll.",
    RoundType.CLOSING: "Give your final position and say whether anything changed your mind.",
}


class Round(MootModel):
    id: str = Field(default_factory=lambda: new_id("rnd"))
    type: RoundType
    name: str = Field(default="", max_length=60)
    instruction: str = ""
    speaking_order: SpeakingOrder = SpeakingOrder.FIXED
    turns_per_agent: int = Field(default=1, ge=1, le=10)
    blind: bool = Field(default=False, description="agents don't see each other's answers")
    mentions_allowed: bool = False
    max_words: int | None = Field(default=None, ge=10, le=2000)
    poll: PollSpec | None = Field(default=None, description="only for vote rounds")

    @model_validator(mode="before")
    @classmethod
    def _fill_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        try:
            round_type = RoundType(data.get("type"))
        except ValueError:
            return data  # field validation will report the bad type
        data = dict(data)
        if not data.get("name"):
            data["name"] = DEFAULT_NAMES[round_type]
        if not data.get("instruction"):
            data["instruction"] = DEFAULT_INSTRUCTIONS[round_type]
        data.setdefault("blind", round_type is RoundType.OPENING)
        data.setdefault("mentions_allowed", round_type in (RoundType.QA, RoundType.FREE))
        if round_type is RoundType.VOTE and data.get("poll") is None:
            data["poll"] = winner_poll_spec()
        return data

    @model_validator(mode="after")
    def _check_poll(self) -> Round:
        if self.type is RoundType.VOTE and self.poll is None:
            raise ValueError("a vote round needs a poll")
        if self.type is not RoundType.VOTE and self.poll is not None:
            raise ValueError("only vote rounds can have a poll")
        return self


class RoundPlan(MootModel):
    rounds: list[Round] = Field(min_length=1)

    @field_validator("rounds")
    @classmethod
    def _unique_ids(cls, value: list[Round]) -> list[Round]:
        ids = [r.id for r in value]
        if len(set(ids)) != len(ids):
            raise ValueError("each round needs a unique id")
        return value

    def __len__(self) -> int:
        return len(self.rounds)

    def add(self, new_round: Round, index: int | None = None) -> None:
        if any(r.id == new_round.id for r in self.rounds):
            raise ValueError(f"a round with id {new_round.id} already exists")
        self.rounds.insert(len(self.rounds) if index is None else index, new_round)

    def remove(self, round_id: str) -> None:
        if len(self.rounds) == 1:
            raise ValueError("a plan needs at least one round")
        self.rounds.pop(self._index_of(round_id))

    def move(self, round_id: str, new_index: int) -> None:
        moved = self.rounds.pop(self._index_of(round_id))
        self.rounds.insert(new_index, moved)

    def _index_of(self, round_id: str) -> int:
        for index, r in enumerate(self.rounds):
            if r.id == round_id:
                return index
        raise ValueError(f"no round with id {round_id}")


# ---------------------------------------------------------------- templates


def classic_debate() -> RoundPlan:
    return RoundPlan(
        rounds=[
            Round(type=RoundType.OPENING),
            Round(type=RoundType.CHALLENGE),
            Round(type=RoundType.REBUTTAL),
            Round(type=RoundType.CLOSING),
            Round(type=RoundType.VOTE),
        ]
    )


def brainstorm() -> RoundPlan:
    return RoundPlan(
        rounds=[
            Round(type=RoundType.OPENING, instruction="Propose your best idea."),
            Round(
                type=RoundType.FREE,
                speaking_order=SpeakingOrder.MODERATOR,
                turns_per_agent=2,
                instruction="Build on, combine or critique the ideas so far.",
            ),
            Round(
                type=RoundType.CLOSING,
                instruction="Name the idea you now think is strongest, and why.",
            ),
        ]
    )


def quick_take() -> RoundPlan:
    return RoundPlan(rounds=[Round(type=RoundType.OPENING)])


TEMPLATES: dict[str, tuple[str, Callable[[], RoundPlan]]] = {
    "classic_debate": ("Classic debate", classic_debate),
    "brainstorm": ("Brainstorm", brainstorm),
    "quick_take": ("Quick take", quick_take),
}
