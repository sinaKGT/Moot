"""Sessions: the setup of a discussion (config) and everything that happened in it (state)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from moot.core.alliances import Alliance, AllianceStatus
from moot.core.base import MootModel, new_id, utc_now
from moot.core.graph import ArgumentGraph, Node, NodeType
from moot.core.messages import MODERATOR_ID, SYSTEM_ID, USER_ID, ChatMessage
from moot.core.moderator import AIModerator, HumanModerator, Moderator
from moot.core.notebook import Notebook
from moot.core.persona import ModelRef, Persona
from moot.core.polls import Poll, PollStatus
from moot.core.round_plan import RoundPlan, classic_debate
from moot.core.rules import SessionRules


class SessionStatus(StrEnum):
    SETUP = "setup"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


class SessionConfig(MootModel):
    """Everything chosen before a discussion starts."""

    topic: str = Field(min_length=1, max_length=2000)
    background: str = ""
    agents: list[Persona] = Field(min_length=2, max_length=12)
    moderator: Moderator = Field(default_factory=AIModerator)
    rules: SessionRules = Field(default_factory=SessionRules)
    round_plan: RoundPlan = Field(default_factory=classic_debate)
    default_model: ModelRef | None = Field(
        default=None, description="used by any agent or AI moderator without its own model"
    )
    graph_builder_model: ModelRef | None = Field(
        default=None, description="model that turns messages into the argument graph"
    )

    @field_validator("topic")
    @classmethod
    def _clean_topic(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("the topic cannot be empty")
        return value

    @model_validator(mode="after")
    def _check(self) -> SessionConfig:
        ids = Counter(agent.id for agent in self.agents)
        if any(count > 1 for count in ids.values()):
            raise ValueError("each agent needs a unique id")

        names = Counter(agent.name.casefold() for agent in self.agents)
        duplicates = sorted(name for name, count in names.items() if count > 1)
        if duplicates:
            raise ValueError(f"agent names must be unique, repeated: {', '.join(duplicates)}")
        if self.moderator_name.casefold() in names:
            raise ValueError(f"the moderator and an agent are both called '{self.moderator_name}'")

        for agent in self.agents:
            if agent.model is None and self.default_model is None:
                raise ValueError(f"agent '{agent.name}' has no model and there is no default model")
        if isinstance(self.moderator, AIModerator) and self.moderator_model() is None:
            raise ValueError("the AI moderator has no model and there is no default model")
        if self.graph_model() is None:
            raise ValueError("choose a model for the argument graph builder, or a default model")
        return self

    @property
    def moderator_name(self) -> str:
        if isinstance(self.moderator, HumanModerator):
            return self.moderator.display_name
        return self.moderator.name

    def speaker_label(self, speaker_id: str | None) -> str:
        """A display name for any speaker id: an agent, the moderator, the user or the system."""
        for agent in self.agents:
            if agent.id == speaker_id:
                return agent.name
        if speaker_id == MODERATOR_ID:
            return self.moderator_name
        if speaker_id == USER_ID:
            return "User"
        if speaker_id == SYSTEM_ID:
            return "System"
        return speaker_id or "—"

    def agent(self, agent_id: str) -> Persona:
        for agent in self.agents:
            if agent.id == agent_id:
                return agent
        raise ValueError(f"no agent with id {agent_id}")

    def agent_by_name(self, name: str) -> Persona | None:
        """Find an agent by name, ignoring case and a leading '@'."""
        wanted = name.strip().removeprefix("@").casefold()
        return next((a for a in self.agents if a.name.casefold() == wanted), None)

    def model_for(self, agent: Persona) -> ModelRef:
        model = agent.model or self.default_model
        if model is None:  # prevented by validation; kept as a safety net
            raise ValueError(f"agent '{agent.name}' has no model")
        return model

    def moderator_model(self) -> ModelRef | None:
        """The AI moderator's model, or None when the user moderates."""
        if isinstance(self.moderator, AIModerator):
            return self.moderator.model or self.default_model
        return None

    def graph_model(self) -> ModelRef | None:
        return self.graph_builder_model or self.moderator_model() or self.default_model


class SessionState(MootModel):
    """A running or finished discussion: its config plus everything that happened."""

    id: str = Field(default_factory=lambda: new_id("ses"))
    config: SessionConfig
    status: SessionStatus = SessionStatus.SETUP
    created_at: datetime = Field(default_factory=utc_now)
    current_round: int = Field(default=0, ge=0)
    messages: list[ChatMessage] = Field(default_factory=list)
    graph: ArgumentGraph = Field(default_factory=ArgumentGraph)
    notebooks: dict[str, Notebook] = Field(default_factory=dict)
    alliances: list[Alliance] = Field(default_factory=list)
    polls: list[Poll] = Field(default_factory=list)
    report: str | None = Field(default=None, description="the AI moderator's final report")

    @model_validator(mode="after")
    def _check(self) -> SessionState:
        agent_ids = {agent.id for agent in self.config.agents}
        unknown = sorted(set(self.notebooks) - agent_ids)
        if unknown:
            raise ValueError(f"notebooks for unknown agents: {', '.join(unknown)}")
        for key, notebook in self.notebooks.items():
            if key != notebook.agent_id:
                raise ValueError(f"notebook stored under {key} belongs to {notebook.agent_id}")
        for agent in self.config.agents:
            self.notebooks.setdefault(agent.id, Notebook(agent_id=agent.id))
        if self.current_round >= len(self.config.round_plan):
            raise ValueError("current_round is past the end of the round plan")
        return self

    @classmethod
    def new(cls, config: SessionConfig) -> SessionState:
        """Start a fresh session; the topic becomes the first node of the argument graph."""
        state = cls(config=config)
        state.graph.add_node(Node(type=NodeType.TOPIC, text=config.topic))
        return state

    def active_alliances(self) -> list[Alliance]:
        return [a for a in self.alliances if a.status is AllianceStatus.ACTIVE]

    def open_polls(self) -> list[Poll]:
        return [p for p in self.polls if p.status is PollStatus.OPEN]

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, data: str | bytes) -> SessionState:
        return cls.model_validate_json(data)
