"""Base class and helpers shared by every Moot data model."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


class MootModel(BaseModel):
    """Strict base model: unknown fields are rejected, so typos in saved files surface early."""

    model_config = ConfigDict(extra="forbid")


def new_id(prefix: str) -> str:
    """Short random id with a readable prefix, e.g. `agt_3f9c1a2b7d4e`."""
    return f"{prefix}_{secrets.token_hex(6)}"


def utc_now() -> datetime:
    return datetime.now(UTC)
