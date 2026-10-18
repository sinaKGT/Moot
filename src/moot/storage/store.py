"""Saving discussions on this computer, so they survive a restart and can be reopened.

One SQLite file (no database server): a row per session with its latest state, and its
events in order, so reopening a discussion replays it exactly as it was shown. Token deltas
are not saved (the finished message holds the whole text), but the last event number is,
so numbering carries on above anything a viewer may have seen.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from moot.core import SessionState, SessionStatus
from moot.core.base import MootModel, utc_now
from moot.core.events import EVENT_ADAPTER, Event
from moot.core.moderator import HumanModerator
from moot.core.session import EndReason
from moot.engine.stream import Listener

SCHEMA_VERSION = 1
DB_NAME = "moot.db"
UNSAVED_EVENTS = frozenset({"token_delta"})
# after these the state hasn't changed in a way worth a new snapshot
NO_SNAPSHOT = frozenset({"token_delta", "turn_started", "vote_cast", "moderator_decision"})

_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    listing TEXT NOT NULL,
    state TEXT NOT NULL
);
CREATE INDEX sessions_by_date ON sessions (created_at);
CREATE TABLE events (
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
CREATE TABLE progress (
    session_id TEXT PRIMARY KEY REFERENCES sessions (id) ON DELETE CASCADE,
    last_seq INTEGER NOT NULL
);
"""


class StoreError(RuntimeError):
    """The saved discussions can't be used, e.g. they were saved by a newer Moot."""


def default_data_dir() -> Path:
    """Where Moot keeps its data: MOOT_DATA_DIR if set, else the usual place for app data."""
    if configured := os.environ.get("MOOT_DATA_DIR"):
        return Path(configured).expanduser()
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "Moot"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Moot"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "moot"


class SessionListing(MootModel):
    """What the list of discussions shows, without loading each one."""

    id: str
    topic: str
    status: SessionStatus
    end_reason: EndReason | None = None
    created_at: datetime
    current_round: int
    rounds: int
    agents: list[str]
    moderator: str
    human_moderator: bool
    decision: bool = False


def listing_of(state: SessionState) -> SessionListing:
    config = state.config
    return SessionListing(
        id=state.id,
        topic=config.topic,
        status=state.status,
        end_reason=state.end_reason,
        created_at=state.created_at,
        current_round=state.current_round,
        rounds=len(config.round_plan),
        agents=[agent.name for agent in config.agents],
        moderator=config.moderator_name,
        human_moderator=isinstance(config.moderator, HumanModerator),
        decision=config.is_decision,
    )


@dataclass
class StoredSession:
    state: SessionState
    events: list[Event]
    last_seq: int = 0  # the last event number used, saved or not


class SessionStore:
    """The saved discussions. `path` is the database file (":memory:" for tests)."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # one server event loop uses it, but tests and uvicorn may create it on another thread
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise StoreError(
                f"The saved discussions in {self.path} were made by a newer version of Moot. "
                "Update Moot, or start it with a different data folder."
            )
        if version == 0:
            with self._db:
                self._db.executescript(_SCHEMA)
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ------------------------------------------------------------------ writing

    def save(self, state: SessionState) -> None:
        """Save the session's current state (its events are saved as they happen)."""
        with self._db:
            self._save(state)

    def record(self, state: SessionState, event: Event) -> None:
        """Save one event, and the state after it, together."""
        save_event = event.type not in UNSAVED_EVENTS
        snapshot = event.type not in NO_SNAPSHOT
        with self._db:
            self._db.execute(
                "INSERT INTO progress (session_id, last_seq) VALUES (?, ?)"
                " ON CONFLICT (session_id) DO UPDATE SET last_seq = excluded.last_seq",
                (state.id, event.seq),
            )
            if save_event:
                self._db.execute(
                    "INSERT OR REPLACE INTO events (session_id, seq, type, data)"
                    " VALUES (?, ?, ?, ?)",
                    (state.id, event.seq, event.type, EVENT_ADAPTER.dump_json(event).decode()),
                )
            if snapshot:
                self._save(state)

    def recorder(self, state: SessionState) -> Listener:
        """An event listener that saves the session as its discussion runs."""
        return lambda event: self.record(state, event)

    def delete(self, session_id: str) -> bool:
        with self._db:
            return self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0

    def close(self) -> None:
        self._db.close()

    def _save(self, state: SessionState) -> None:
        self._db.execute(
            "INSERT INTO sessions (id, created_at, updated_at, listing, state)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (id) DO UPDATE SET updated_at = excluded.updated_at,"
            " listing = excluded.listing, state = excluded.state",
            (
                state.id,
                state.created_at.isoformat(),
                utc_now().isoformat(),
                listing_of(state).model_dump_json(),
                state.model_dump_json(),
            ),
        )

    # ------------------------------------------------------------------ reading

    def listings(self) -> list[SessionListing]:
        """Every saved discussion, newest first."""
        rows = self._db.execute("SELECT listing FROM sessions ORDER BY created_at DESC")
        return [SessionListing.model_validate_json(listing) for (listing,) in rows]

    def load(self, session_id: str) -> StoredSession | None:
        row = self._db.execute("SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        events = [
            EVENT_ADAPTER.validate_json(data)
            for (data,) in self._db.execute(
                "SELECT data FROM events WHERE session_id = ? ORDER BY seq", (session_id,)
            )
        ]
        progress = self._db.execute(
            "SELECT last_seq FROM progress WHERE session_id = ?", (session_id,)
        ).fetchone()
        return StoredSession(
            state=SessionState.model_validate_json(row[0]),
            events=events,
            last_seq=max([progress[0] if progress else 0, *(e.seq for e in events)]),
        )

    def __contains__(self, session_id: object) -> bool:
        found = self._db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return found is not None


def saved_events(events: Iterable[Event]) -> list[Event]:
    """The events worth keeping in a saved or exported discussion."""
    return [event for event in events if event.type not in UNSAVED_EVENTS]
