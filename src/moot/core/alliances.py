"""Declared alliances: agents can openly propose, accept, refuse and leave alliances."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field

from moot.core.base import MootModel, new_id


class AllianceError(ValueError):
    """An invalid alliance action, such as accepting an invitation you never received."""


class AllianceStatus(StrEnum):
    PROPOSED = "proposed"  # waiting for at least one invitee to accept
    ACTIVE = "active"
    DISSOLVED = "dissolved"


class Alliance(MootModel):
    id: str = Field(default_factory=lambda: new_id("all"))
    name: str | None = None
    proposed_by: str
    members: list[str]
    invited: list[str] = Field(default_factory=list)
    refused: list[str] = Field(default_factory=list)
    status: AllianceStatus = AllianceStatus.PROPOSED

    @classmethod
    def propose(
        cls, proposer: str, invitees: Sequence[str], *, name: str | None = None
    ) -> Alliance:
        invited = list(dict.fromkeys(invitees))
        if not invited:
            raise AllianceError("invite at least one agent")
        if proposer in invited:
            raise AllianceError("an agent cannot invite itself")
        return cls(name=name, proposed_by=proposer, members=[proposer], invited=invited)

    def accept(self, agent_id: str) -> None:
        self._require_open()
        if agent_id not in self.invited:
            raise AllianceError(f"{agent_id} was not invited")
        self.invited.remove(agent_id)
        self.members.append(agent_id)
        self.status = AllianceStatus.ACTIVE

    def refuse(self, agent_id: str) -> None:
        self._require_open()
        if agent_id not in self.invited:
            raise AllianceError(f"{agent_id} was not invited")
        self.invited.remove(agent_id)
        self.refused.append(agent_id)
        if not self.invited and len(self.members) < 2:
            self.status = AllianceStatus.DISSOLVED

    def leave(self, agent_id: str) -> None:
        self._require_open()
        if agent_id not in self.members:
            raise AllianceError(f"{agent_id} is not a member")
        self.members.remove(agent_id)
        if len(self.members) < 2 and (self.status is AllianceStatus.ACTIVE or not self.invited):
            self.status = AllianceStatus.DISSOLVED

    def is_member(self, agent_id: str) -> bool:
        return agent_id in self.members and self.status is AllianceStatus.ACTIVE

    def _require_open(self) -> None:
        if self.status is AllianceStatus.DISSOLVED:
            raise AllianceError("the alliance has been dissolved")
