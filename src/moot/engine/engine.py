"""The discussion engine: runs the round plan turn by turn and reports everything as events."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from moot.core import (
    MODERATOR_ID,
    SYSTEM_ID,
    AIModerator,
    ChatMessage,
    Node,
    NodeType,
    Persona,
    Round,
    RoundType,
    SessionState,
    SessionStatus,
    SpeakerKind,
    SpeakingOrder,
    TokenUsage,
    new_id,
)
from moot.core.events import (
    EngineNotice,
    ErrorEvent,
    GraphUpdated,
    ModeratorDecision,
    NotebookUpdated,
    RoundEnded,
    RoundStarted,
    RuleViolation,
    SessionEnded,
    SessionPaused,
    SessionResumed,
    SessionStarted,
    TokenDelta,
    TurnCompleted,
    TurnStarted,
)
from moot.engine.mentions import find_mentions
from moot.engine.moderator import ModeratorBrain
from moot.engine.pool import ProviderPool
from moot.engine.prompts import build_agent_messages
from moot.engine.rule_checks import check_rules
from moot.engine.stream import EventStream
from moot.engine.text import NamePrefixStripper, TagFilter, TrailingSpaceHolder
from moot.memory import GraphBuilder, apply_notes, parse_notes, pending_items, split_trailing_notes
from moot.providers import (
    ChatRequest,
    Message,
    Provider,
    ProviderError,
    StructuredOutputError,
    TextDelta,
    Usage,
)

DEFAULT_MAX_CONSECUTIVE_FAILURES = 3


class _TooManyFailures(Exception):
    pass


@dataclass
class _Spoken:
    text: str
    captured: dict[str, str]
    usage: Usage | None


def _token_usage(usage: Usage | None) -> TokenUsage | None:
    if usage is None:
        return None
    return TokenUsage(prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens)


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
        moderator_enabled: bool = True,
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
        self._build_graph = build_graph
        self._graph_builder = graph_builder or GraphBuilder()
        self._graph_queue: asyncio.Queue[ChatMessage | None] = asyncio.Queue()
        self._graph_busy = False
        ai_moderated = isinstance(state.config.moderator, AIModerator)
        self._moderator = ModeratorBrain() if ai_moderated and moderator_enabled else None
        self._reminders: dict[str, list[str]] = {}
        self._last_speaker: str | None = None

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
        reason: str | None = None
        try:
            if self._moderator:
                await self._moderator_opening()
            rounds = self.state.config.round_plan.rounds
            for index, current in enumerate(rounds):
                if self._stopped:
                    break
                self.state.current_round = index
                await self._run_round(index, current)
                if self._moderator and not self._stopped and index < len(rounds) - 1:
                    await self._moderator_turn(
                        self._moderator.summary_messages(self.state, index), index
                    )
            reason = "stopped" if self._stopped else "completed"
        except _TooManyFailures:
            reason = "error"
        finally:
            reason = reason or "stopped"
            if graph_worker is not None:
                await self._finish_graph(graph_worker, cancel=reason != "completed")
            if reason == "completed" and self._moderator:
                await self._final_report()
            self.state.status = SessionStatus.FINISHED
            self.events.emit(SessionEnded(reason=reason))  # type: ignore[arg-type]
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
        moderator_model = config.moderator_model()
        if self._moderator and moderator_model is not None:
            self._providers.get(moderator_model)

    # ------------------------------------------------------------------ rounds

    async def _run_round(self, index: int, current: Round) -> None:
        self.events.emit(RoundStarted(round_index=index, round_id=current.id))
        if current.type is RoundType.VOTE:
            self._notice("Vote rounds are not available yet; skipping this round.", index)
        else:
            turns = {agent.id: 0 for agent in self.state.config.agents}
            total = len(turns) * current.turns_per_agent
            for _ in range(total):
                await asyncio.sleep(0)  # let subscribers and controls run between turns
                await self._running.wait()
                if self._stopped:
                    return
                agent = await self._next_speaker(index, current, turns)
                await self._agent_turn(agent, index, current, turns[agent.id])
                turns[agent.id] += 1
        self.events.emit(RoundEnded(round_index=index))

    async def _next_speaker(self, index: int, current: Round, turns: dict[str, int]) -> Persona:
        agents = self.state.config.agents
        candidates = [a for a in agents if turns[a.id] < current.turns_per_agent]
        if current.speaking_order is SpeakingOrder.FIXED or len(candidates) == 1:
            return min(candidates, key=lambda a: (turns[a.id], agents.index(a)))
        if current.speaking_order is SpeakingOrder.MODERATOR and self._moderator:
            chosen = await self._moderator_pick(index, current, candidates, turns)
            if chosen is not None:
                return chosen
        return self._fair_pick(index, current, candidates, turns)

    async def _moderator_pick(
        self, index: int, current: Round, candidates: list[Persona], turns: dict[str, int]
    ) -> Persona | None:
        assert self._moderator is not None
        model = self.state.config.moderator_model()
        if model is None:
            return None
        try:
            choice = await self._moderator.pick_next(
                self.state, index, current, candidates, turns, self._providers.get(model), model
            )
        except (ProviderError, StructuredOutputError) as exc:
            self.events.emit(ErrorEvent(message=f"The moderator could not choose a speaker: {exc}"))
            return None
        agent = self.state.config.agent_by_name(choice.next)
        if agent is None or agent not in candidates:
            return None
        if agent.id == self._last_speaker and len(candidates) > 1:
            return None  # nobody speaks twice in a row while others are waiting
        reason = choice.reason.strip() or "chosen by the moderator"
        self.events.emit(ModeratorDecision(next_speaker_id=agent.id, reason=reason))
        return agent

    def _fair_pick(
        self, index: int, current: Round, candidates: list[Persona], turns: dict[str, int]
    ) -> Persona:
        """Without a moderator's choice: whoever has the most to answer, avoiding the same
        speaker twice in a row, then whoever has spoken least."""
        agents = self.state.config.agents
        hidden = index if current.blind else None
        pending = {a.id: pending_items(self.state, a, hidden) for a in candidates}
        chosen = min(
            candidates,
            key=lambda a: (
                a.id == self._last_speaker,
                -pending[a.id],
                turns[a.id],
                agents.index(a),
            ),
        )
        count = pending[chosen.id]
        reason = (
            f"{chosen.name} has {count} challenge{'s' if count != 1 else ''} or question"
            f"{'s' if count != 1 else ''} to answer"
            if count
            else f"{chosen.name} has spoken least in this round"
        )
        self.events.emit(ModeratorDecision(next_speaker_id=chosen.id, reason=reason))
        return chosen

    # ------------------------------------------------------------- agent turns

    async def _agent_turn(self, agent: Persona, index: int, current: Round, turn: int) -> None:
        config = self.state.config
        message_id = new_id("msg")
        self.events.emit(TurnStarted(message_id=message_id, speaker_id=agent.id, round_index=index))

        model = config.model_for(agent)
        reminders = self._reminders.pop(agent.id, [])
        request = ChatRequest(
            model=model.model,
            messages=build_agent_messages(self.state, agent, index, current, turn, reminders),
            temperature=model.temperature,
        )
        try:
            spoken = await self._speak(message_id, agent.name, request, self._providers.get(model))
        except ProviderError as exc:
            self._turn_failed(agent, index, message_id, str(exc))
            return

        text = spoken.text
        notes = spoken.captured.get("notes", "")
        if not notes:
            text, notes = split_trailing_notes(text)
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
            usage=_token_usage(spoken.usage),
        )
        self.state.messages.append(message)
        self._last_speaker = agent.id
        self.events.emit(TurnCompleted(message=message))
        self._update_notebook(agent, notes, index)
        self._check_rules(agent, current, message, index)
        if self._build_graph:
            self._graph_queue.put_nowait(message)  # mapped in the background

    async def _speak(
        self, message_id: str, name: str, request: ChatRequest, provider: Provider
    ) -> _Spoken:
        """Stream an answer as TokenDelta events, hiding <think> blocks, private sections,
        a leading "Name:" and trailing blank lines."""
        tags = TagFilter(capture=("notes", "plan"))
        stripper = NamePrefixStripper(name)
        spaces = TrailingSpaceHolder()
        parts: list[str] = []
        usage: Usage | None = None

        def show(text: str) -> None:
            visible = spaces.feed(stripper.feed(tags.feed(text)))
            if visible:
                parts.append(visible)
                self.events.emit(TokenDelta(message_id=message_id, text=visible))

        async for event in provider.stream(request):
            if isinstance(event, TextDelta):
                show(event.text)
            else:
                usage = event.usage
        for tail in (stripper.feed(tags.flush()), stripper.flush()):
            visible = spaces.feed(tail)
            if visible:
                parts.append(visible)
                self.events.emit(TokenDelta(message_id=message_id, text=visible))
        spaces.flush()
        return _Spoken(text="".join(parts).strip(), captured=tags.captured, usage=usage)

    def _check_rules(
        self, agent: Persona, current: Round, message: ChatMessage, index: int
    ) -> None:
        broken = check_rules(self.state.config, current, message.text)
        if not broken:
            return
        for rule in broken:
            self.events.emit(RuleViolation(agent_id=agent.id, rule=rule, message_id=message.id))
        self._reminders.setdefault(agent.id, []).extend(broken)
        if self._moderator:
            self._moderator_says(f"{agent.name}, a reminder: {' '.join(broken)}", index)

    def _update_notebook(self, agent: Persona, notes: str, index: int) -> None:
        """Merge the agent's private notes; if they are missing or unreadable, keep the old ones."""
        if not notes.strip():
            return
        try:
            update = parse_notes(notes)
        except ValueError:
            return
        apply_notes(self.state, agent, update, index)
        self.events.emit(NotebookUpdated(agent_id=agent.id))

    def _turn_failed(self, agent: Persona, index: int, message_id: str, error: str) -> None:
        self._failures += 1
        self.events.emit(ErrorEvent(message=error, agent_id=agent.id, message_id=message_id))
        self._notice(f"{agent.name} could not answer ({error}).", index)
        if self._failures >= self._max_failures:
            self._notice("Too many failed turns in a row; ending the discussion.", index)
            raise _TooManyFailures

    # --------------------------------------------------------------- moderator

    async def _moderator_turn(
        self, messages: list[Message], round_index: int | None
    ) -> tuple[ChatMessage, _Spoken] | None:
        """The AI moderator speaks. A failure is reported but never stops the discussion."""
        config = self.state.config
        model = config.moderator_model()
        if model is None:
            return None
        message_id = new_id("msg")
        self.events.emit(
            TurnStarted(message_id=message_id, speaker_id=MODERATOR_ID, round_index=round_index)
        )
        request = ChatRequest(model=model.model, messages=messages, temperature=model.temperature)
        try:
            spoken = await self._speak(
                message_id, config.moderator_name, request, self._providers.get(model)
            )
        except ProviderError as exc:
            self.events.emit(
                ErrorEvent(message=f"The moderator could not speak: {exc}", message_id=message_id)
            )
            return None
        if not spoken.text:
            self.events.emit(
                ErrorEvent(message="The moderator returned an empty answer", message_id=message_id)
            )
            return None
        message = ChatMessage(
            id=message_id,
            speaker_id=MODERATOR_ID,
            speaker_kind=SpeakerKind.MODERATOR,
            text=spoken.text,
            round_index=round_index,
            usage=_token_usage(spoken.usage),
        )
        self.state.messages.append(message)
        self.events.emit(TurnCompleted(message=message))
        return message, spoken

    async def _moderator_opening(self) -> None:
        assert self._moderator is not None
        result = await self._moderator_turn(self._moderator.opening_messages(self.state), None)
        if result is None:
            return
        message, spoken = result
        questions = self._moderator.parse_plan(spoken.captured.get("plan", ""), message.text)
        node_ids = [
            self.state.graph.add_node(
                Node(
                    type=NodeType.SUBQUESTION,
                    text=question,
                    speaker_id=MODERATOR_ID,
                    message_id=message.id,
                )
            ).id
            for question in questions
        ]
        if node_ids:
            self.events.emit(GraphUpdated(node_ids=node_ids))

    async def _final_report(self) -> None:
        assert self._moderator is not None
        result = await self._moderator_turn(self._moderator.report_messages(self.state), None)
        if result is not None:
            self.state.report = result[0].text

    def _moderator_says(self, text: str, index: int | None) -> None:
        """A short moderator line that needs no model, such as a rule reminder."""
        message = ChatMessage(
            speaker_id=MODERATOR_ID,
            speaker_kind=SpeakerKind.MODERATOR,
            text=text,
            round_index=index,
        )
        self.events.emit(
            TurnStarted(message_id=message.id, speaker_id=MODERATOR_ID, round_index=index)
        )
        self.events.emit(TokenDelta(message_id=message.id, text=text))
        self.state.messages.append(message)
        self.events.emit(TurnCompleted(message=message))

    def _notice(self, text: str, index: int | None) -> None:
        message = ChatMessage(
            speaker_id=SYSTEM_ID, speaker_kind=SpeakerKind.SYSTEM, text=text, round_index=index
        )
        self.state.messages.append(message)
        self.events.emit(TurnCompleted(message=message))

    # ------------------------------------------------------------------ graph

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
            pending = self._graph_queue.qsize() + (1 if self._graph_busy else 0)
            if pending:
                left = f"{pending} message{'s' if pending > 1 else ''} left"
                self.events.emit(EngineNotice(message=f"Finishing the argument map ({left})..."))
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
