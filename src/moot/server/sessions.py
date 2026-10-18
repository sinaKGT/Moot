"""Running discussions inside the server: create them, start them, send them commands.

Sessions live in memory for now; saving and reopening them comes later.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from moot.core import SessionConfig, SessionState, SessionStatus
from moot.engine import DiscussionEngine, ProviderPool
from moot.providers import PRESETS, Provider, create_provider

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
    environment); tests pass a fake one.
    """

    def __init__(
        self,
        provider_factory: ProviderFactory | None = None,
        *,
        engine_options: dict | None = None,
    ) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._provider_factory = provider_factory
        self._engine_options = engine_options or {}

    # ----------------------------------------------------------------- sessions

    def create(self, config: SessionConfig) -> SessionRecord:
        """Set up a session without starting it. Raises ValueError for unknown providers."""
        if self._provider_factory is None:
            self._check_presets(config)
        state = SessionState.new(config)
        pool = ProviderPool(factory=self._provider_factory) if self._provider_factory else None
        engine = DiscussionEngine(state, providers=pool, **self._engine_options)
        record = SessionRecord(state=state, engine=engine)
        self._sessions[state.id] = record
        return record

    def get(self, session_id: str) -> SessionRecord:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise SessionNotFound(session_id) from None

    def all(self) -> list[SessionRecord]:
        return sorted(self._sessions.values(), key=lambda r: r.state.created_at, reverse=True)

    def start(self, session_id: str) -> SessionRecord:
        """Start the discussion in the background. Raises RuntimeError if already started."""
        record = self.get(session_id)
        if record.started or record.state.status is not SessionStatus.SETUP:
            raise RuntimeError("this session has already been started")
        record.engine.check_providers()
        record.task = asyncio.create_task(self._run(record), name=f"moot-session-{record.id}")
        return record

    async def delete(self, session_id: str) -> None:
        """Remove a session, ending it at once if it is still running."""
        record = self.get(session_id)
        del self._sessions[session_id]
        await self._cancel(record)

    async def shutdown(self) -> None:
        for record in list(self._sessions.values()):
            await self._cancel(record)

    # ----------------------------------------------------------------- providers

    def provider(self, preset_id: str, base_url: str | None = None) -> Provider:
        """A provider for listing models. Raises ValueError for an unknown preset."""
        if self._provider_factory is not None:
            return self._provider_factory(preset_id)
        return create_provider(preset_id, base_url=base_url)

    # ------------------------------------------------------------------ helpers

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
