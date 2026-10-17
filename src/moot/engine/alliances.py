"""Alliance actions written by agents: propose, accept, refuse or leave.

An agent adds a hidden <alliance>{...}</alliance> line to its answer. The engine applies
it to the session and reports each change as an event; the alliance itself is public.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, field_validator

from moot.core import Alliance, AllianceError, MootModel, Persona, SessionState
from moot.core.events import AllianceChanged
from moot.providers import extract_json_object

ALLIANCE_INSTRUCTIONS = """Alliances (optional and public): you may team up with participants \
who share your view. To propose, accept, refuse or leave an alliance, say so in your reply and \
also add one line in exactly this form:
<alliance>{"action": "propose", "with": ["Name"], "goal": "your shared position in a few \
words"}</alliance>
The other actions: {"action": "accept", "from": "Name"}, {"action": "refuse", "from": "Name"}, \
{"action": "leave", "reason": "why"}. Leave the line out when nothing changes. An alliance \
never overrides honesty: don't back a claim you think is wrong."""

_ACTIONS = {
    "propose": "propose",
    "invite": "propose",
    "ally": "propose",
    "accept": "accept",
    "join": "accept",
    "refuse": "refuse",
    "decline": "refuse",
    "reject": "refuse",
    "leave": "leave",
    "break": "leave",
    "quit": "leave",
    "exit": "leave",
}


class AllianceAction(MootModel, extra="ignore"):
    action: Literal["propose", "accept", "refuse", "leave"]
    with_: list[str] = Field(default_factory=list, validation_alias=AliasChoices("with", "to"))
    from_: str = Field(default="", validation_alias=AliasChoices("from", "by"))
    goal: str = ""
    reason: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, value: object) -> object:
        if isinstance(value, str):
            return _ACTIONS.get(value.strip().lower(), value)
        return value

    @field_validator("with_", mode="before")
    @classmethod
    def _names(cls, value: object) -> object:
        if isinstance(value, str):
            return [value]
        return value or []

    @field_validator("from_", "goal", "reason", mode="before")
    @classmethod
    def _text(cls, value: object) -> object:
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return value or ""


def parse_alliance_action(text: str) -> AllianceAction:
    """Read an action; raises ValueError if it can't be understood."""
    return AllianceAction.model_validate(extract_json_object(text))


def apply_alliance_action(
    state: SessionState, agent: Persona, action: AllianceAction, round_index: int | None
) -> list[AllianceChanged]:
    """Apply the action and return the changes as events (not yet emitted).

    Raises AllianceError, worded for the agent, when the action isn't possible.
    """
    return _Actions(state, agent, round_index).apply(action)


class _Actions:
    def __init__(self, state: SessionState, agent: Persona, round_index: int | None) -> None:
        self.state = state
        self.agent = agent
        self.round_index = round_index
        self.changes: list[AllianceChanged] = []

    def apply(self, action: AllianceAction) -> list[AllianceChanged]:
        if action.action == "propose":
            self._propose(action.with_ + ([action.from_] if action.from_ else []), action.goal)
        elif action.action == "accept":
            self._accept(self._invitation(action.from_ or _first(action.with_)))
        elif action.action == "refuse":
            alliance = self._invitation(action.from_ or _first(action.with_))
            alliance.refuse(self.agent.id, round_index=self.round_index, reason=action.reason)
            self._report(alliance, "refused", reason=action.reason)
        else:
            self._leave(action.from_ or _first(action.with_), action.reason)
        return self.changes

    # ---------------------------------------------------------------- actions

    def _propose(self, names: list[str], goal: str) -> None:
        invitees = [self._agent_id(name) for name in names]
        invitees = [a for a in dict.fromkeys(invitees) if a != self.agent.id]
        if not invitees:
            raise AllianceError("name at least one other participant to ally with")

        # Someone named already invited this agent: proposing back means accepting.
        for alliance in self.state.alliances:
            if (
                alliance.is_open
                and self.agent.id in alliance.invited
                and set(invitees) & set(alliance.members)
            ):
                self._accept(alliance)
                return

        own = self._alliance_of(self.agent.id)
        if own is not None:
            if all(a in own.members for a in invitees):
                raise AllianceError(f"you are already allied with {self._names(invitees)}")
            new = own.invite(self.agent.id, invitees, round_index=self.round_index)
            self._report(own, "proposed", targets=new)
            return

        alliance = Alliance.propose(
            self.agent.id, invitees, goal=goal, round_index=self.round_index
        )
        self.state.alliances.append(alliance)
        self._report(alliance, "proposed", targets=invitees)

    def _accept(self, alliance: Alliance) -> None:
        current = self._alliance_of(self.agent.id)
        if current is not None and current.id != alliance.id:  # switching sides
            self._leave_alliance(current, "joined another alliance")
        alliance.accept(self.agent.id, round_index=self.round_index)
        self._report(alliance, "accepted")

    def _leave(self, name: str, reason: str) -> None:
        mine = [a for a in self.state.alliances if a.is_open and self.agent.id in a.members]
        if name:
            named = self._agent_id(name)
            mine = [a for a in mine if named in a.members or named in a.invited]
        if not mine:
            raise AllianceError("you are not in an alliance" + (f" with {name}" if name else ""))
        self._leave_alliance(mine[-1], reason)

    def _leave_alliance(self, alliance: Alliance, reason: str) -> None:
        alliance.leave(self.agent.id, round_index=self.round_index, reason=reason)
        self._report(alliance, "left", reason=reason)

    # ---------------------------------------------------------------- helpers

    def _invitation(self, name: str) -> Alliance:
        waiting = [a for a in self.state.alliances if a.is_open and self.agent.id in a.invited]
        if name:
            named = self._agent_id(name)
            waiting = [a for a in waiting if named in a.members]
        if not waiting:
            raise AllianceError(
                "you have no alliance invitation" + (f" from {name}" if name else "")
            )
        return waiting[-1]

    def _alliance_of(self, agent_id: str) -> Alliance | None:
        return next(
            (a for a in reversed(self.state.alliances) if a.is_open and agent_id in a.members),
            None,
        )

    def _agent_id(self, name: str) -> str:
        found = self.state.config.agent_by_name(name)
        if found is None:
            raise AllianceError(f"there is no participant called {name}")
        return found.id

    def _names(self, agent_ids: list[str]) -> str:
        return ", ".join(self.state.config.speaker_label(a) for a in agent_ids)

    def _report(
        self, alliance: Alliance, change: str, *, targets: list[str] | None = None, reason: str = ""
    ) -> None:
        self.changes.append(
            AllianceChanged(
                change=change,  # type: ignore[arg-type]
                agent_id=self.agent.id,
                targets=targets or [],
                reason=reason.strip(),
                round_index=self.round_index,
                alliance=alliance.model_copy(deep=True),
            )
        )
        if alliance.ended_round is not None and alliance.history[-1].change == "dissolved":
            self.changes.append(
                AllianceChanged(
                    change="dissolved",
                    agent_id=None,
                    round_index=self.round_index,
                    alliance=alliance.model_copy(deep=True),
                )
            )


def _first(names: list[str]) -> str:
    return names[0] if names else ""
