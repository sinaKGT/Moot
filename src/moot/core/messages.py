"""Chat messages: everything said in a session, by agents, the moderator or the user."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from moot.core.base import MootModel, new_id, utc_now

# Fixed speaker ids for the non-agent seats.
MODERATOR_ID = "moderator"
USER_ID = "user"
SYSTEM_ID = "system"


class SpeakerKind(StrEnum):
    AGENT = "agent"
    MODERATOR = "moderator"  # the AI moderator, or the user when they moderate
    USER = "user"  # the user interjecting while the AI moderates
    SYSTEM = "system"  # engine notices, e.g. "Round 2 started"


class TokenUsage(MootModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    estimated: bool = Field(default=False, description="True when the provider didn't report it")


class ChatMessage(MootModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    speaker_id: str
    speaker_kind: SpeakerKind
    text: str
    round_index: int | None = None
    mentions: list[str] = Field(default_factory=list, description="ids of agents @mentioned")
    reply_to: str | None = Field(default=None, description="id of the message being answered")
    created_at: datetime = Field(default_factory=utc_now)
    usage: TokenUsage | None = None
    model: str | None = Field(default=None, description="provider/model that wrote it")
    seconds: float | None = Field(default=None, ge=0, description="how long the answer took")
    sources: list[str] = Field(
        default_factory=list, description="the agent's own files it was briefed from, this turn"
    )
