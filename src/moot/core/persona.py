"""Agent personas: who an agent is, what it believes and wants, and which model it uses."""

from __future__ import annotations

from pydantic import Field, field_validator

from moot.core.base import MootModel, new_id

HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


class ModelRef(MootModel):
    """Which model to call: a provider preset id plus the provider's model id."""

    provider: str = Field(min_length=1, description="provider preset id, e.g. 'openrouter'")
    model: str = Field(min_length=1, description="model id as listed by the provider")
    temperature: float | None = Field(default=None, ge=0, le=2)


class Traits(MootModel):
    """Behaviour sliders, each from 0 to 1 (shown as a radar chart in the UI)."""

    persuadability: float = Field(0.5, ge=0, le=1, description="0 holds firm, 1 easily persuaded")
    verbosity: float = Field(0.5, ge=0, le=1, description="0 concise, 1 detailed")
    alliance_openness: float = Field(0.5, ge=0, le=1, description="0 lone wolf, 1 seeks allies")


class Persona(MootModel):
    id: str = Field(default_factory=lambda: new_id("agt"))
    name: str = Field(min_length=1, max_length=40)
    color: str = Field(default="#60a5fa", pattern=HEX_COLOR)
    role: str = Field(default="", description="short title, e.g. 'Economist'")
    viewpoint: str = Field(default="", description="what this agent believes and argues for")
    motivation: str = Field(default="", description="what it wants from the discussion")
    expertise: str = Field(default="", description="background knowledge")
    thinking_style: str = Field(default="", description="how it argues")
    traits: Traits = Field(default_factory=Traits)
    model: ModelRef | None = Field(default=None, description="None = use the session default")
    prompt_override: str | None = Field(default=None, description="advanced: full custom prompt")

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be empty")
        if "@" in value or "\n" in value:
            raise ValueError("name cannot contain '@' or line breaks")
        return value
