"""The discussion engine: runs the round plan turn by turn and reports everything as events."""

from __future__ import annotations

import asyncio

from moot.core import (
    SYSTEM_ID,
    ChatMessage,
    Persona,
    Round,
    RoundType,
    SessionState,
    SessionStatus,
    SpeakerKind,
    TokenUsage,
    new_id,
)
from moot.core.events import (
    ErrorEvent,
    RoundEnded,
    RoundStarted,
    SessionEnded,
    SessionPaused,
    SessionResumed,
    SessionStarted,
    TokenDelta,
    TurnCompleted,
    TurnStarted,
)
from moot.engine.pool import ProviderPool
from moot.engine.prompts import build_agent_messages
from moot.engine.stream import EventStream
from moot.engine.text import NamePrefixStripper
from moot.providers import ChatRequest, ProviderError, TextDelta, Usage

DEFAULT_MAX_CONSECUTIVE_FAILURES = 3


class _TooManyFailures(Exception):
    pass


class DiscussionEngine:
    """Runs one session. Create it, subscribe to `events`, then `await run()`.

    `pause()`, `resume()` and `stop()` can be called while it runs; they take effect
    between turns.
    """

    def __init__(
        self,
        state: SessionState,
        *,
        providers: ProviderPool | None = None,
        events: EventStream | None = None,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    ) -> None:
        self.state = state
        self.events = events or EventStream()
        self._providers = providers or ProviderPool()
        self._owns_providers = providers is None
        self._max_failures = max_consecutive_failures
        self._failures = 0
        self._stopped = False
        self._running = asyncio.Event()
        self._running.set()

    # ----------------------------------------------------------------- controls

    def pause(self) -> None:
        if self._running.is_set() and self.state.status is SessionStatus.RUNNING:
            self._running.clear()
            self.state.status = SessionStatus.PAUSED
            self.events.emit(SessionPaused())

    def resume(self) -> None:
        if not self._running.is_set():
            self.state.status = SessionStatus.RUNNING
            self._running.set()
            self.events.emit(SessionResumed())

    def stop(self) -> None:
        """Finish after the current turn."""
        self._stopped = True
        self._running.set()  # let a paused engine reach the stop check

    # --------------------------------------------------------------------- run

    async def run(self) -> SessionState:
        if self.state.status is not SessionStatus.SETUP:
            raise RuntimeError("this session has already been run")
        self._check_providers()

        self.state.status = SessionStatus.RUNNING
        self.events.emit(SessionStarted(session_id=self.state.id))
        reason = "completed"
        try:
            for index, current in enumerate(self.state.config.round_plan.rounds):
                if self._stopped:
                    break
                self.state.current_round = index
                await self._run_round(index, current)
            if self._stopped:
                reason = "stopped"
        except _TooManyFailures:
            reason = "error"
        finally:
            self.state.status = SessionStatus.FINISHED
            self.events.emit(SessionEnded(reason=reason))
            self.events.close()
            if self._owns_providers:
                await self._providers.aclose()
        return self.state

    def _check_providers(self) -> None:
        """Fail before the discussion starts if any model's provider is unknown."""
        config = self.state.config
        for agent in config.agents:
            self._providers.get(config.model_for(agent))

    async def _run_round(self, index: int, current: Round) -> None:
        self.events.emit(RoundStarted(round_index=index, round_id=current.id))
        if current.type is RoundType.VOTE:
            self._notice("Vote rounds are not available yet; skipping this round.", index)
        else:
            for turn in range(current.turns_per_agent):
                for agent in self._speaking_order(current):
                    await asyncio.sleep(0)  # let subscribers and controls run between turns
                    await self._running.wait()
                    if self._stopped:
                        return
                    await self._agent_turn(agent, index, current, turn)
        self.events.emit(RoundEnded(round_index=index))

    def _speaking_order(self, current: Round) -> list[Persona]:
        # Panel order for now; moderator-chosen and free order arrive with the AI moderator.
        return list(self.state.config.agents)

    async def _agent_turn(self, agent: Persona, index: int, current: Round, turn: int) -> None:
        config = self.state.config
        message_id = new_id("msg")
        self.events.emit(TurnStarted(message_id=message_id, speaker_id=agent.id, round_index=index))

        model = config.model_for(agent)
        request = ChatRequest(
            model=model.model,
            messages=build_agent_messages(self.state, agent, index, current, turn),
            temperature=model.temperature,
        )
        stripper = NamePrefixStripper(agent.name)
        parts: list[str] = []
        usage: Usage | None = None
        try:
            async for event in self._providers.get(model).stream(request):
                if isinstance(event, TextDelta):
                    self._emit_text(message_id, stripper.feed(event.text), parts)
                else:
                    usage = event.usage
            self._emit_text(message_id, stripper.flush(), parts)
        except ProviderError as exc:
            self._turn_failed(agent, index, message_id, str(exc))
            return

        text = "".join(parts).strip()
        if not text:
            self._turn_failed(agent, index, message_id, f"{agent.name} returned an empty answer")
            return

        self._failures = 0
        message = ChatMessage(
            id=message_id,
            speaker_id=agent.id,
            speaker_kind=SpeakerKind.AGENT,
            text=text,
            round_index=index,
            usage=TokenUsage(
                prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens
            )
            if usage
            else None,
        )
        self.state.messages.append(message)
        self.events.emit(TurnCompleted(message=message))

    def _emit_text(self, message_id: str, text: str, parts: list[str]) -> None:
        if text:
            parts.append(text)
            self.events.emit(TokenDelta(message_id=message_id, text=text))

    def _turn_failed(self, agent: Persona, index: int, message_id: str, error: str) -> None:
        self._failures += 1
        self.events.emit(ErrorEvent(message=error, agent_id=agent.id, message_id=message_id))
        self._notice(f"{agent.name} could not answer ({error}).", index)
        if self._failures >= self._max_failures:
            self._notice("Too many failed turns in a row; ending the discussion.", index)
            raise _TooManyFailures

    def _notice(self, text: str, index: int | None) -> None:
        message = ChatMessage(
            speaker_id=SYSTEM_ID, speaker_kind=SpeakerKind.SYSTEM, text=text, round_index=index
        )
        self.state.messages.append(message)
        self.events.emit(TurnCompleted(message=message))
