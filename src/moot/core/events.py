"""Events: what the engine reports as a discussion runs. The UI and the API listen to these."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from moot.core.alliances import Alliance
from moot.core.base import MootModel, utc_now
from moot.core.messages import ChatMessage
from moot.core.polls import Poll, PollResult


class EventBase(MootModel):
    seq: int = Field(default=0, ge=0, description="position in the session's event stream")
    at: datetime = Field(default_factory=utc_now)


class SessionStarted(EventBase):
    type: Literal["session_started"] = "session_started"
    session_id: str


class SessionPaused(EventBase):
    type: Literal["session_paused"] = "session_paused"


class SessionResumed(EventBase):
    type: Literal["session_resumed"] = "session_resumed"


class SessionEnded(EventBase):
    type: Literal["session_ended"] = "session_ended"
    reason: Literal["completed", "stopped", "error"]


class RoundStarted(EventBase):
    type: Literal["round_started"] = "round_started"
    round_index: int
    round_id: str


class RoundEnded(EventBase):
    type: Literal["round_ended"] = "round_ended"
    round_index: int


class TurnStarted(EventBase):
    type: Literal["turn_started"] = "turn_started"
    message_id: str
    speaker_id: str
    round_index: int | None = None


class TokenDelta(EventBase):
    type: Literal["token_delta"] = "token_delta"
    message_id: str
    text: str


class TurnCompleted(EventBase):
    type: Literal["turn_completed"] = "turn_completed"
    message: ChatMessage


class ModeratorDecision(EventBase):
    type: Literal["moderator_decision"] = "moderator_decision"
    next_speaker_id: str | None
    reason: str


class GraphUpdated(EventBase):
    type: Literal["graph_updated"] = "graph_updated"
    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)


class NotebookUpdated(EventBase):
    type: Literal["notebook_updated"] = "notebook_updated"
    agent_id: str


class AllianceChanged(EventBase):
    type: Literal["alliance_changed"] = "alliance_changed"
    change: Literal["proposed", "accepted", "refused", "left", "dissolved"]
    agent_id: str
    alliance: Alliance


class PollOpened(EventBase):
    type: Literal["poll_opened"] = "poll_opened"
    poll: Poll


class VoteCast(EventBase):
    type: Literal["vote_cast"] = "vote_cast"
    poll_id: str
    voter_id: str
    option_id: str


class PollClosed(EventBase):
    type: Literal["poll_closed"] = "poll_closed"
    poll_id: str
    results: list[PollResult]


class RuleViolation(EventBase):
    type: Literal["rule_violation"] = "rule_violation"
    agent_id: str
    rule: str
    message_id: str | None = None


class ErrorEvent(EventBase):
    type: Literal["error"] = "error"
    message: str
    agent_id: str | None = None


Event = Annotated[
    SessionStarted
    | SessionPaused
    | SessionResumed
    | SessionEnded
    | RoundStarted
    | RoundEnded
    | TurnStarted
    | TokenDelta
    | TurnCompleted
    | ModeratorDecision
    | GraphUpdated
    | NotebookUpdated
    | AllianceChanged
    | PollOpened
    | VoteCast
    | PollClosed
    | RuleViolation
    | ErrorEvent,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(data: str | bytes | dict[str, Any]) -> Event:
    """Rebuild an event from JSON (or a dict), picking the right class from its `type`."""
    if isinstance(data, dict):
        return EVENT_ADAPTER.validate_python(data)
    return EVENT_ADAPTER.validate_json(data)
