"""Running discussions inside the server: create them, start them, send them commands.

With a store, every session is saved as it runs and reopened from disk after a restart;
discussions that were running when the server stopped end there, as stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from moot.core import SessionConfig, SessionState, SessionStatus
from moot.core.events import EngineNotice, SessionEnded
from moot.engine import DiscussionEngine, EventStream, ProviderPool
from moot.providers import PRESETS, Provider, create_provider
from moot.storage import SessionListing, SessionStore, listing_of

log = logging.getLogger(__name__)

ProviderFactory = Callable[[str], Provider]


class SessionNotFound(KeyError):
    """No session with that id."""


@dataclass
class SessionRecord:
    state: SessionState
    engine: DiscussionEngine
    task: asyncio.Task[SessionState] | None = field(default=None, repr=False)
    error: str | None = None

    @property
    def id(self) -> str:
        return self.state.id

    @property
    def started(self) -> bool:
        return self.task is not None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


class SessionManager:
    """Owns the sessions of one server.

    `provider_factory` replaces the real providers (from presets and API keys in the
    environment); tests pass a fake one. With a `store`, sessions are saved and reopened;
    the manager closes it on shutdown.
    """

    def __init__(
        self,
        provider_factory: ProviderFactory | None = None,
        *,
        engine_options: dict | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._provider_factory = provider_factory
        self._engine_options = engine_options or {}
        self._store = store
        if store is not None:
            self._end_interrupted()

    @property
    def store(self) -> SessionStore | None:
        return self._store

    # ----------------------------------------------------------------- sessions

    def create(self, config: SessionConfig) -> SessionRecord:
        """Set up a session without starting it. Raises ValueError for unknown providers."""
        if self._provider_factory is None:
            self._check_presets(config)
        state = SessionState.new(config)
        if self._store is not None:
            self._store.save(state)
        return self._open(state, EventStream())

    def get(self, session_id: str) -> SessionRecord:
        """A session, reopened from the store if it isn't in memory."""
        if session_id in self._sessions:
            return self._sessions[session_id]
        stored = self._store.load(session_id) if self._store is not None else None
        if stored is None:
            raise SessionNotFound(session_id)
        finished = stored.state.status is SessionStatus.FINISHED
        events = EventStream.restore(stored.events, closed=finished, last_seq=stored.last_seq)
        return self._open(stored.state, events)

    def all(self) -> list[SessionRecord]:
        """The sessions in memory (see `listings` for every saved one)."""
        return sorted(self._sessions.values(), key=lambda r: r.state.created_at, reverse=True)

    def listings(self) -> list[tuple[SessionListing, SessionRecord | None]]:
        """Every session, newest first: live ones with their record, saved ones without
        loading them."""
        found = {
            record.id: (listing_of(record.state), record) for record in self._sessions.values()
        }
        if self._store is not None:
            for listing in self._store.listings():
                found.setdefault(listing.id, (listing, None))
        return sorted(found.values(), key=lambda item: item[0].created_at, reverse=True)

    def start(self, session_id: str) -> SessionRecord:
        """Start the discussion in the background. Raises RuntimeError if already started."""
        record = self.get(session_id)
        if record.started or record.state.status is not SessionStatus.SETUP:
            raise RuntimeError("this session has already been started")
        record.engine.check_providers()
        record.task = asyncio.create_task(self._run(record), name=f"moot-session-{record.id}")
        return record

    async def delete(self, session_id: str) -> None:
        """Remove a session (also from the store), ending it at once if it is running."""
        record = self._sessions.pop(session_id, None)
        saved = self._store.delete(session_id) if self._store is not None else False
        if record is None and not saved:
            raise SessionNotFound(session_id)
        if record is not None:
            await self._cancel(record)

    async def shutdown(self) -> None:
        """End running discussions (they are saved as stopped) and close the store."""
        for record in list(self._sessions.values()):
            await self._cancel(record)
        if self._store is not None:
            self._store.close()

    # ----------------------------------------------------------------- providers

    def provider(self, preset_id: str, base_url: str | None = None) -> Provider:
        """A provider for listing models. Raises ValueError for an unknown preset."""
        if self._provider_factory is not None:
            return self._provider_factory(preset_id)
        return create_provider(preset_id, base_url=base_url)

    # ------------------------------------------------------------------ helpers

    def _open(self, state: SessionState, events: EventStream) -> SessionRecord:
        pool = ProviderPool(factory=self._provider_factory) if self._provider_factory else None
        if self._store is not None and not events.closed:
            events.listen(self._store.recorder(state))
        engine = DiscussionEngine(state, providers=pool, events=events, **self._engine_options)
        record = SessionRecord(state=state, engine=engine)
        self._sessions[state.id] = record
        return record

    def _end_interrupted(self) -> None:
        """Discussions saved as running were cut off when Moot stopped: end them there."""
        assert self._store is not None
        for listing in self._store.listings():
            if listing.status not in (SessionStatus.RUNNING, SessionStatus.PAUSED):
                continue
            stored = self._store.load(listing.id)
            if stored is None:
                continue
            state = stored.state
            events = EventStream.restore(stored.events, closed=False, last_seq=stored.last_seq)
            events.listen(self._store.recorder(state))
            state.status = SessionStatus.FINISHED
            state.end_reason = "stopped"
            events.emit(
                EngineNotice(
                    message="Moot stopped while this discussion was running, so it ends here."
                )
            )
            events.emit(SessionEnded(reason="stopped"))
            events.close()
            log.info("session %s was interrupted and has been ended", state.id)

    @staticmethod
    def _check_presets(config: SessionConfig) -> None:
        refs = [config.model_for(agent) for agent in config.agents]
        refs += [ref for ref in (config.moderator_model(), config.graph_model()) if ref]
        for ref in refs:
            if ref.provider not in PRESETS:
                choices = ", ".join(PRESETS)
                raise ValueError(f"Unknown provider '{ref.provider}'. Choose one of: {choices}.")

    async def _run(self, record: SessionRecord) -> SessionState:
        try:
            return await record.engine.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # reported on the session, never crashes the server
            record.error = f"{type(exc).__name__}: {exc}"
            log.exception("session %s failed", record.id)
            return record.state

    @staticmethod
    async def _cancel(record: SessionRecord) -> None:
        if record.task is not None and not record.task.done():
            record.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await record.task
