"""The moderator seat: filled either by an AI moderator or by the user."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from moot.core.base import MootModel
from moot.core.persona import ModelRef


class AIModerator(MootModel):
    kind: Literal["ai"] = "ai"
    name: str = Field(default="Moderator", min_length=1, max_length=40)
    model: ModelRef | None = Field(default=None, description="None = use the session default")


class HumanModerator(MootModel):
    kind: Literal["human"] = "human"
    display_name: str = Field(min_length=1, max_length=40)
    role: str = Field(default="Moderator", max_length=60, description="e.g. 'Judge', 'Journalist'")


Moderator = Annotated[AIModerator | HumanModerator, Field(discriminator="kind")]
