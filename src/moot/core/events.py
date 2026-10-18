"""Events: what the engine reports as a discussion runs. The UI and the API listen to these."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from moot.core.alliances import Alliance, AllianceChange
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
    reply_to: str | None = Field(default=None, description="the message this turn answers")
    purpose: Literal["opening", "summary", "conclusion"] | None = Field(
        default=None, description="for the AI moderator's turns"
    )


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


class GraphLink(MootModel):
    """A new link in the argument graph, as who-did-what-to-whom (for the round table)."""

    type: str = Field(description="supports, challenges, answers, refines or concedes_to")
    source_speaker: str | None
    target_speaker: str | None
    message_id: str | None = Field(default=None, description="the message the new point is from")


class GraphUpdated(EventBase):
    type: Literal["graph_updated"] = "graph_updated"
    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    links: list[GraphLink] = Field(default_factory=list)


class NotebookUpdated(EventBase):
    type: Literal["notebook_updated"] = "notebook_updated"
    agent_id: str
    verdict: Literal["yes", "no", "unsure"] | None = None
    choice: str | None = None
    confidence: float | None = None


class AllianceChanged(EventBase):
    type: Literal["alliance_changed"] = "alliance_changed"
    change: AllianceChange
    agent_id: str | None = Field(description="who acted; None when the alliance just ended")
    targets: list[str] = Field(default_factory=list, description="who was invited")
    reason: str = ""
    round_index: int | None = None
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


class EngineNotice(EventBase):
    """A passing status message for the UI (not part of the discussion)."""

    type: Literal["engine_notice"] = "engine_notice"
    message: str


class WaitingForModerator(EventBase):
    """The engine waits for the human moderator: to start the next round, to give someone
    the floor, or to finish once the round plan is done."""

    type: Literal["waiting_for_moderator"] = "waiting_for_moderator"
    waiting_for: Literal["start_round", "next_speaker", "finish"]
    round_index: int | None = None


class ErrorEvent(EventBase):
    type: Literal["error"] = "error"
    message: str
    agent_id: str | None = None
    message_id: str | None = Field(default=None, description="the turn that failed, if any")


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
    | EngineNotice
    | WaitingForModerator
    | ErrorEvent,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(data: str | bytes | dict[str, Any]) -> Event:
    """Rebuild an event from JSON (or a dict), picking the right class from its `type`."""
    if isinstance(data, dict):
        return EVENT_ADAPTER.validate_python(data)
    return EVENT_ADAPTER.validate_json(data)
