"""Private notebooks: each agent's own stance, judgements and strategy, hidden from the others."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from moot.core.base import MootModel

Verdict = Literal["yes", "no", "unsure"]


class StanceSnapshot(MootModel):
    round_index: int = Field(ge=0)
    verdict: Verdict | None = None
    stance: str
    confidence: float = Field(ge=0, le=1)


class Notebook(MootModel):
    agent_id: str
    verdict: Verdict | None = Field(default=None, description="answer to the topic question")
    stance: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)
    key_arguments: list[str] = Field(default_factory=list)
    concessions: list[str] = Field(default_factory=list)
    judgements: dict[str, str] = Field(
        default_factory=dict, description="other agent id -> how this agent rates their arguments"
    )
    allies: list[str] = Field(default_factory=list)
    opponents: list[str] = Field(default_factory=list)
    strategy: str = ""
    questions_to_ask: list[str] = Field(default_factory=list)
    history: list[StanceSnapshot] = Field(default_factory=list, description="stance per round")

    def snapshot(self, round_index: int) -> None:
        """Record the current stance for a round, replacing any earlier snapshot of that round."""
        self.history = [s for s in self.history if s.round_index != round_index]
        self.history.append(
            StanceSnapshot(
                round_index=round_index,
                verdict=self.verdict,
                stance=self.stance,
                confidence=self.confidence,
            )
        )
        self.history.sort(key=lambda s: s.round_index)
