"""The discussion engine: runs the round plan turn by turn and reports everything as events."""

from __future__ import annotations

import asyncio
import contextlib

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
    EngineNotice,
    ErrorEvent,
    GraphUpdated,
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
from moot.engine.mentions import find_mentions
from moot.engine.pool import ProviderPool
from moot.engine.prompts import build_agent_messages
from moot.engine.stream import EventStream
from moot.engine.text import NamePrefixStripper
from moot.memory import GraphBuilder
from moot.providers import (
    ChatRequest,
    ProviderError,
    StructuredOutputError,
    TextDelta,
    Usage,
)

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
        build_graph: bool = True,
        graph_builder: GraphBuilder | None = None,
    ) -> None:
        self.state = state
        self.events = events or EventStream()
        self._providers = providers or ProviderPool()
        self._owns_providers = providers is None
        self._max_failures = max_consecutive_failures
        self._build_graph = build_graph
        self._graph_builder = graph_builder or GraphBuilder()
        self._graph_queue: asyncio.Queue[ChatMessage | None] = asyncio.Queue()
        self._graph_busy = False
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
        graph_worker = asyncio.create_task(self._graph_worker()) if self._build_graph else None
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
            if graph_worker is not None:
                await self._finish_graph(graph_worker, cancel=self._stopped)
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
        graph_model = config.graph_model()
        if self._build_graph and graph_model is not None:
            self._providers.get(graph_model)

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
            mentions=find_mentions(text, config.agents, exclude=agent.id),
            usage=TokenUsage(
                prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens
            )
            if usage
            else None,
        )
        self.state.messages.append(message)
        self.events.emit(TurnCompleted(message=message))
        if self._build_graph:
            self._graph_queue.put_nowait(message)  # mapped in the background

    async def _graph_worker(self) -> None:
        """Map messages into the argument graph one at a time, in the order they were said,
        while the discussion carries on."""
        while True:
            message = await self._graph_queue.get()
            if message is None:
                return
            self._graph_busy = True
            try:
                await self._update_graph(message)
            except Exception as exc:  # a bug here must not break the discussion
                self.events.emit(
                    ErrorEvent(
                        message=f"Argument graph not updated: {exc!r}", message_id=message.id
                    )
                )
            finally:
                self._graph_busy = False

    async def _finish_graph(self, worker: asyncio.Task[None], *, cancel: bool) -> None:
        """Wait for pending graph work, or drop it when the discussion was stopped."""
        if cancel:
            worker.cancel()
        else:
            if self._graph_busy or not self._graph_queue.empty():
                self.events.emit(EngineNotice(message="Finishing the argument map..."))
            self._graph_queue.put_nowait(None)
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    async def _update_graph(self, message: ChatMessage) -> None:
        """Map the message into the argument graph. A failure here never stops the discussion."""
        model = self.state.config.graph_model()
        if model is None:
            return
        try:
            update = await self._graph_builder.update(
                self.state, message, self._providers.get(model), model
            )
        except (ProviderError, StructuredOutputError) as exc:
            self.events.emit(
                ErrorEvent(message=f"Argument graph not updated: {exc}", message_id=message.id)
            )
            return
        self.events.emit(GraphUpdated(node_ids=update.node_ids, edge_ids=update.edge_ids))

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
