"""Declared alliances: agents can openly propose, accept, refuse and leave alliances.

Every action is kept in the alliance's history, which gives the timeline for the
alliance monitor and lets agents call out a broken alliance.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from pydantic import Field

from moot.core.base import MootModel, new_id

AllianceChange = Literal["proposed", "accepted", "refused", "left", "dissolved"]


class AllianceError(ValueError):
    """An invalid alliance action, such as accepting an invitation you never received."""


class AllianceStatus(StrEnum):
    PROPOSED = "proposed"  # waiting for at least one invitee to accept
    ACTIVE = "active"
    DISSOLVED = "dissolved"


class AllianceStep(MootModel):
    change: AllianceChange
    agent_id: str | None = Field(description="who acted; None when the alliance just ended")
    targets: list[str] = Field(default_factory=list, description="who was invited")
    round_index: int | None = None
    reason: str = ""


class Alliance(MootModel):
    id: str = Field(default_factory=lambda: new_id("all"))
    name: str | None = None
    goal: str = Field(default="", description="the shared position, in a few words")
    proposed_by: str
    members: list[str]
    invited: list[str] = Field(default_factory=list)
    refused: list[str] = Field(default_factory=list)
    left: list[str] = Field(default_factory=list)
    status: AllianceStatus = AllianceStatus.PROPOSED
    history: list[AllianceStep] = Field(default_factory=list)

    @classmethod
    def propose(
        cls,
        proposer: str,
        invitees: Sequence[str],
        *,
        name: str | None = None,
        goal: str = "",
        round_index: int | None = None,
    ) -> Alliance:
        invited = list(dict.fromkeys(invitees))
        if not invited:
            raise AllianceError("invite at least one agent")
        if proposer in invited:
            raise AllianceError("an agent cannot invite itself")
        alliance = cls(name=name, goal=goal.strip(), proposed_by=proposer, members=[proposer])
        alliance.invited = invited
        alliance._record("proposed", proposer, round_index, targets=invited)
        return alliance

    def invite(
        self, agent_id: str, invitees: Sequence[str], *, round_index: int | None = None
    ) -> list[str]:
        """A member invites more agents. Returns who was newly invited."""
        self._require_open()
        if agent_id not in self.members:
            raise AllianceError(f"{agent_id} is not a member")
        new = [
            a
            for a in dict.fromkeys(invitees)
            if a != agent_id and a not in self.members and a not in self.invited
        ]
        if not new:
            raise AllianceError("everyone named is already a member or invited")
        self.invited += new
        self.refused = [a for a in self.refused if a not in new]
        self._record("proposed", agent_id, round_index, targets=new)
        return new

    def accept(self, agent_id: str, *, round_index: int | None = None) -> None:
        self._require_open()
        if agent_id not in self.invited:
            raise AllianceError(f"{agent_id} was not invited")
        self.invited.remove(agent_id)
        self.members.append(agent_id)
        self.status = AllianceStatus.ACTIVE
        self._record("accepted", agent_id, round_index)

    def refuse(self, agent_id: str, *, round_index: int | None = None, reason: str = "") -> None:
        self._require_open()
        if agent_id not in self.invited:
            raise AllianceError(f"{agent_id} was not invited")
        self.invited.remove(agent_id)
        self.refused.append(agent_id)
        self._record("refused", agent_id, round_index, reason=reason)
        if not self.invited and len(self.members) < 2:
            self._dissolve(round_index)

    def leave(self, agent_id: str, *, round_index: int | None = None, reason: str = "") -> None:
        self._require_open()
        if agent_id not in self.members:
            raise AllianceError(f"{agent_id} is not a member")
        self.members.remove(agent_id)
        self.left.append(agent_id)
        self._record("left", agent_id, round_index, reason=reason)
        if not self.members or (
            len(self.members) < 2 and (self.status is AllianceStatus.ACTIVE or not self.invited)
        ):
            self._dissolve(round_index)

    def is_member(self, agent_id: str) -> bool:
        return agent_id in self.members and self.status is AllianceStatus.ACTIVE

    @property
    def is_open(self) -> bool:
        return self.status is not AllianceStatus.DISSOLVED

    @property
    def formed_round(self) -> int | None:
        """The round in which the first invitee accepted."""
        return next((s.round_index for s in self.history if s.change == "accepted"), None)

    @property
    def ended_round(self) -> int | None:
        return next((s.round_index for s in self.history if s.change == "dissolved"), None)

    @property
    def everyone(self) -> list[str]:
        """Everyone who has been a member, in the order they joined."""
        joined = [self.proposed_by] + [s.agent_id for s in self.history if s.change == "accepted"]
        return [a for a in dict.fromkeys(joined) if a is not None]

    def _dissolve(self, round_index: int | None) -> None:
        self.status = AllianceStatus.DISSOLVED
        self._record("dissolved", None, round_index)

    def _record(
        self,
        change: AllianceChange,
        agent_id: str | None,
        round_index: int | None,
        *,
        targets: Sequence[str] = (),
        reason: str = "",
    ) -> None:
        self.history.append(
            AllianceStep(
                change=change,
                agent_id=agent_id,
                targets=list(targets),
                round_index=round_index,
                reason=reason.strip(),
            )
        )

    def _require_open(self) -> None:
        if self.status is AllianceStatus.DISSOLVED:
            raise AllianceError("the alliance has been dissolved")
