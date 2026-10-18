"""The discussion engine: runs the round plan turn by turn and reports everything as events."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from moot.core import (
    MODERATOR_ID,
    SYSTEM_ID,
    USER_ID,
    AIModerator,
    AllianceError,
    ChatMessage,
    HumanModerator,
    Node,
    NodeType,
    Persona,
    Poll,
    PollSpec,
    PollStatus,
    Round,
    RoundType,
    SessionState,
    SessionStatus,
    SpeakerKind,
    SpeakingOrder,
    TokenUsage,
    new_id,
    winner_poll_spec,
)
from moot.core.events import (
    EngineNotice,
    ErrorEvent,
    GraphUpdated,
    ModeratorDecision,
    NotebookUpdated,
    PollClosed,
    PollOpened,
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
    VoteCast,
    WaitingForModerator,
)
from moot.engine.alliances import apply_alliance_action, parse_alliance_action
from moot.engine.mentions import find_mentions
from moot.engine.moderator import ModeratorBrain, audience_question
from moot.engine.pool import ProviderPool
from moot.engine.prompts import build_agent_messages
from moot.engine.rule_checks import check_rules
from moot.engine.stream import EventStream
from moot.engine.text import NamePrefixStripper, TagFilter, TrailingSpaceHolder
from moot.engine.voting import (
    VoteChoice,
    match_choice,
    parse_poll_spec,
    retry_message,
    vote_messages,
)
from moot.memory import GraphBuilder, apply_notes, parse_notes, pending_items, split_trailing_notes
from moot.providers import (
    ChatRequest,
    Message,
    Provider,
    ProviderError,
    StructuredOutputError,
    TextDelta,
    Usage,
    complete_structured,
)

DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_MAX_REPLY_CHAIN = 2  # a question, its answer, and one answer back


class _TooManyFailures(Exception):
    pass


@dataclass
class _UserMessage:
    text: str
    to: list[str]


@dataclass
class _Reminder:
    agent_id: str
    text: str


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
    between turns. `say()` posts a message from the user, `open_poll()` runs a poll and
    `remind()` reminds an agent, between turns and also while paused. `give_floor()`,
    `end_round()`, `next_round()` and `finish()` steer the round plan.

    Human moderator: the engine waits for them before each round (`next_round()`), for
    every speaker in rounds where the moderator chooses (`give_floor()`), and at the end
    (`next_round()` or `finish()`). It announces each wait with a WaitingForModerator
    event. There is no AI help: no summaries, speaker choices, reports or automatic
    reminders.

    @mentions: when an agent writes "@Ben ...?" in a round that allows mentions, Ben answers
    straight away, before the round goes on. A reply can lead to one answer back
    (`max_reply_chain`), and agent-to-agent replies per round are capped
    (`max_replies_per_round`, by default as many as the round's normal turns), so two agents
    can't keep each other talking. Agents the user @mentions always answer.
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
        max_reply_chain: int = DEFAULT_MAX_REPLY_CHAIN,
        max_replies_per_round: int | None = None,
        max_moderator_polls: int = 1,
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
        self._max_reply_chain = max_reply_chain
        self._max_replies_per_round = max_replies_per_round
        self._round_replies = 0
        self._inbox: deque[_UserMessage | _Reminder | PollSpec] = deque()
        self._max_moderator_polls = max_moderator_polls
        self._moderator_polls = 0
        self._wake = asyncio.Event()  # set when something may let a waiting engine continue
        self._human = isinstance(state.config.moderator, HumanModerator)
        self._floor: deque[str | None] = deque()  # who gets the floor next (None = fair pick)
        self._advance: str | None = None  # "next" or "end", set by next_round() / end_round()
        self._finishing = False
        self.waiting_for: str | None = None  # what the engine is waiting for, if anything
        self._turns: dict[str, int] = {}  # turns used in the running round (empty between rounds)

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
            self._wake.set()
            self.events.emit(SessionResumed())

    def stop(self) -> None:
        """Finish after the current turn."""
        self._stopped = True
        self._running.set()  # let a paused engine reach the stop check
        self._wake.set()

    def say(self, text: str, *, to: Sequence[str] = ()) -> None:
        """Post a message from the user (from the human moderator when they moderate).

        It appears between turns, also while the session is paused, and every agent it
        @mentions answers right away. `to` adds agents (names or ids) to answer even if the
        text doesn't @mention them.
        """
        if self.state.status is SessionStatus.FINISHED:
            raise RuntimeError("this session has finished")
        text = text.strip()
        if not text:
            raise ValueError("the message is empty")
        self._inbox.append(_UserMessage(text, [self._agent_id(name) for name in to]))
        self._wake.set()

    def open_poll(self, spec: PollSpec | None = None) -> None:
        """Run a poll between turns, also while paused: every agent picks one option.
        Without a spec it asks who made the strongest case."""
        if self.state.status is SessionStatus.FINISHED:
            raise RuntimeError("this session has finished")
        self._inbox.append(spec or winner_poll_spec())
        self._wake.set()

    def remind(self, agent: str, text: str) -> None:
        """Remind an agent of something, publicly; it sees the reminder before its next turn."""
        if self.state.status is SessionStatus.FINISHED:
            raise RuntimeError("this session has finished")
        text = text.strip()
        if not text:
            raise ValueError("the reminder is empty")
        self._inbox.append(_Reminder(self._agent_id(agent), text))
        self._wake.set()

    def give_floor(self, agent: str | None = None) -> None:
        """Let an agent (name or id) speak next. Without one, the engine picks fairly."""
        if self.state.status is SessionStatus.FINISHED:
            raise RuntimeError("this session has finished")
        self._floor.append(self._agent_id(agent) if agent else None)
        self._wake.set()

    def end_round(self) -> None:
        """End the current round after the current turn. A human moderator then starts the
        next one with `next_round()`."""
        self._advance = "end"
        self._wake.set()

    def next_round(self) -> None:
        """Move on: end the current round (if one is running) and start the next one. In the
        last round it just ends the round; once the plan is done, it finishes the discussion."""
        self._advance = "next"
        self._wake.set()

    def finish(self) -> None:
        """End the discussion normally after the current turn, skipping any rounds left.
        Unlike `stop()`, it counts as completed, so the AI moderator still reports."""
        self._finishing = True
        self._running.set()
        self._wake.set()

    def _people_id(self) -> str:
        """Who the user speaks as: the moderator when they moderate, otherwise the user."""
        if isinstance(self.state.config.moderator, HumanModerator):
            return MODERATOR_ID
        return USER_ID

    def _agent_id(self, name_or_id: str) -> str:
        config = self.state.config
        agent = config.agent_by_name(name_or_id)
        if agent is None:
            try:
                agent = config.agent(name_or_id)
            except ValueError:
                raise ValueError(f"there is no participant called {name_or_id!r}") from None
        return agent.id

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
                if self._human:
                    await self._round_gate(index, current, "start_round")
                if self._stopped or self._finishing:
                    break
                self.state.current_round = index
                await self._run_round(index, current)
                last_round = index == len(rounds) - 1
                if self._advance == "end" or not self._human or last_round:
                    self._advance = None  # a human's "next" opens the next round's gate
                if (
                    self._moderator
                    and not self._stopped
                    and not self._finishing
                    and index < len(rounds) - 1
                ):
                    offer_poll = (
                        self._moderator_polls < self._max_moderator_polls
                        and rounds[index + 1].type is not RoundType.VOTE
                    )
                    summary = await self._moderator_turn(
                        self._moderator.summary_messages(self.state, index, offer_poll=offer_poll),
                        index,
                        "summary",
                    )
                    if summary is not None and offer_poll:
                        await self._moderator_poll(summary[1].captured.get("poll", ""), index)
            if self._human and not self._stopped and not self._finishing:
                last = len(rounds) - 1
                await self._round_gate(last, rounds[last], "finish")
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
        self._round_replies = 0
        turns = {agent.id: 0 for agent in self.state.config.agents}
        self._turns = turns
        try:
            await self._round_body(index, current, turns)
        finally:
            self._turns = {}
        if self._stopped:
            return
        self.events.emit(RoundEnded(round_index=index))

    async def _round_body(self, index: int, current: Round, turns: dict[str, int]) -> None:
        if current.type is RoundType.VOTE and current.poll is not None:
            await self._between_turns(index, current)
            if self._stopped:
                return
            if not self._round_cut_short():
                await self._run_poll(current.poll, index, current, opened_by=MODERATOR_ID)
        elif self._human and current.speaking_order is SpeakingOrder.MODERATOR:
            await self._moderated_turns(index, current, turns)
        else:
            await self._automatic_turns(index, current, turns)
        await self._between_turns(index, current)  # anything the user did during the last turn

    def _round_cut_short(self) -> bool:
        return self._stopped or self._finishing or self._advance is not None

    async def _automatic_turns(self, index: int, current: Round, turns: dict[str, int]) -> None:
        """Everyone gets their turns, in the round's speaking order. Someone given the floor
        speaks next, even beyond their turns."""
        while any(count < current.turns_per_agent for count in turns.values()):
            await self._between_turns(index, current)
            if self._round_cut_short():
                return
            if self._floor:
                agent = self._granted_speaker(index, current, turns)
            elif all(count >= current.turns_per_agent for count in turns.values()):
                return  # answers to the user used up the last turns
            else:
                agent = await self._next_speaker(index, current, turns)
            await self._take_turn(agent, index, current, turns)

    async def _moderated_turns(self, index: int, current: Round, turns: dict[str, int]) -> None:
        """A human moderator gives the floor, one turn at a time, until they move on."""
        while True:
            await self._wait_until(
                index,
                current,
                lambda: bool(self._floor) or self._round_cut_short(),
                waiting_for="next_speaker",
            )
            if self._round_cut_short():
                return
            agent = self._granted_speaker(index, current, turns)
            await self._take_turn(agent, index, current, turns)

    def _granted_speaker(self, index: int, current: Round, turns: dict[str, int]) -> Persona:
        config = self.state.config
        requested = self._floor.popleft()
        if requested is None:
            agents = config.agents
            waiting = [a for a in agents if turns[a.id] < current.turns_per_agent]
            return self._fair_pick(index, current, waiting or list(agents), turns)
        agent = config.agent(requested)
        self.events.emit(ModeratorDecision(next_speaker_id=agent.id, reason=""))
        return agent

    async def _take_turn(
        self, agent: Persona, index: int, current: Round, turns: dict[str, int]
    ) -> None:
        message = await self._agent_turn(agent, index, current, turns[agent.id])
        turns[agent.id] += 1
        if message is not None:
            await self._answer_mentions(message, index, current)

    async def _round_gate(self, index: int, current: Round, waiting_for: str) -> None:
        """Wait for a human moderator to start round `index` or, at the end, to finish."""

        def ready() -> bool:
            if self._advance == "end":
                self._advance = None  # nothing is running to end
            return self._advance == "next" or self._finishing

        await self._wait_until(index, current, ready, waiting_for=waiting_for)
        if self._advance == "next":
            self._advance = None

    async def _between_turns(self, index: int, current: Round) -> None:
        """Post what the user said (and get the answers), and wait here while paused."""
        await self._wait_until(index, current, lambda: True)

    async def _wait_until(
        self,
        index: int,
        current: Round,
        ready: Callable[[], bool],
        *,
        waiting_for: str | None = None,
    ) -> None:
        """Handle what the user sends (messages, polls, reminders) until `ready()` is true,
        also while paused. Announces a wait for the human moderator once."""
        await asyncio.sleep(0)  # let subscribers and controls run
        announced = False
        try:
            while not self._stopped:
                if self._inbox:
                    await self._handle_inbox(self._inbox.popleft(), index, current)
                elif self._running.is_set() and ready():
                    return
                else:
                    if waiting_for and not announced and self._running.is_set():
                        self.waiting_for = waiting_for
                        self.events.emit(
                            WaitingForModerator(
                                waiting_for=waiting_for,  # type: ignore[arg-type]
                                round_index=index,
                            )
                        )
                        announced = True
                    self._wake.clear()
                    await self._wake.wait()
        finally:
            self.waiting_for = None

    async def _handle_inbox(
        self, item: _UserMessage | _Reminder | PollSpec, index: int, current: Round
    ) -> None:
        if isinstance(item, PollSpec):
            await self._run_poll(item, index, current, opened_by=self._people_id())
        elif isinstance(item, _Reminder):
            agent = self.state.config.agent(item.agent_id)
            speaker_id = self._people_id()
            kind = SpeakerKind.MODERATOR if speaker_id == MODERATOR_ID else SpeakerKind.USER
            self._post_instant(
                ChatMessage(
                    speaker_id=speaker_id,
                    speaker_kind=kind,
                    text=f"{agent.name}, a reminder: {item.text}",
                    round_index=index,
                )
            )
            self._reminders.setdefault(agent.id, []).append(item.text)
        else:
            await self._post_user_message(item.text, item.to, index, current)

    async def _post_user_message(
        self, text: str, to: list[str], index: int, current: Round
    ) -> None:
        config = self.state.config
        speaker_id = self._people_id()
        kind = SpeakerKind.MODERATOR if speaker_id == MODERATOR_ID else SpeakerKind.USER
        mentions = find_mentions(text, config.agents)
        mentions += [agent_id for agent_id in to if agent_id not in mentions]
        message = ChatMessage(
            speaker_id=speaker_id,
            speaker_kind=kind,
            text=text,
            round_index=index,
            mentions=mentions,
        )
        self._post_instant(message)
        if self._build_graph:
            self._graph_queue.put_nowait(message)
        await self._answer_mentions(message, index, current)

    async def _answer_mentions(
        self, question: ChatMessage, index: int, current: Round, chain: int = 0
    ) -> None:
        """Agents @mentioned in a message answer straight away, within the loop guard.

        If the agent hasn't had its turn in this round yet, the answer is its turn: it answers
        first, then makes its own point, and doesn't speak again in a separate turn.
        """
        from_agent = question.speaker_kind is SpeakerKind.AGENT
        if from_agent and (not current.mentions_allowed or current.blind):
            return
        if chain >= self._max_reply_chain:
            return
        config = self.state.config
        limit = self._max_replies_per_round
        if limit is None:
            limit = len(config.agents) * current.turns_per_agent
        for agent_id in question.mentions:
            if self._stopped:
                return
            if agent_id == question.speaker_id:
                continue
            if from_agent:
                if self._round_replies >= limit:
                    return  # the question is answered in a normal turn instead
                self._round_replies += 1
            used = self._turns.get(agent_id)
            own_turn = (
                used is not None
                and used < current.turns_per_agent
                and current.type is not RoundType.VOTE
            )
            reply = await self._agent_turn(
                config.agent(agent_id),
                index,
                current,
                used or 0,
                reply_to=question,
                own_turn=own_turn,
            )
            if reply is not None:
                if own_turn:
                    self._turns[agent_id] += 1
                await self._answer_mentions(reply, index, current, chain + 1)

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

    # ------------------------------------------------------------------- polls

    async def _run_poll(self, spec: PollSpec, index: int, current: Round, opened_by: str) -> Poll:
        """Open a poll, let every agent vote at the same time, then close it."""
        config = self.state.config
        earlier = next(
            (
                p
                for p in reversed(self.state.polls)
                if p.status is PollStatus.CLOSED
                and p.kind is spec.kind
                and p.question.casefold() == spec.question.casefold()
            ),
            None,
        )
        poll = Poll.open(
            spec,
            config.agents,
            opened_by=opened_by,
            round_index=index,
            previous_id=earlier.id if earlier else None,
        )
        self.state.polls.append(poll)
        self.events.emit(PollOpened(poll=poll.model_copy(deep=True)))
        ballots = [
            asyncio.create_task(self._ballot(agent, poll, index, current))
            for agent in config.agents
        ]
        try:
            for finished in asyncio.as_completed(ballots):
                agent, option_id = await finished
                if option_id is None:
                    self._notice(f"{agent.name} did not vote.", index)
                    continue
                poll.cast_vote(agent.id, option_id)
                self.events.emit(VoteCast(poll_id=poll.id, voter_id=agent.id, option_id=option_id))
        finally:
            for ballot in ballots:
                ballot.cancel()
        poll.close()
        self.events.emit(PollClosed(poll_id=poll.id, results=poll.results()))
        return poll

    async def _ballot(
        self, agent: Persona, poll: Poll, index: int, current: Round
    ) -> tuple[Persona, str | None]:
        """One agent's vote: an option id, or None if it couldn't vote."""
        allowed = poll.allowed_options(agent.id)
        if len(allowed) == 1:  # e.g. two agents in a winner poll
            return agent, allowed[0].id
        model = self.state.config.model_for(agent)
        provider = self._providers.get(model)
        messages = vote_messages(self.state, agent, poll, allowed, index, current)
        for _attempt in range(2):
            request = ChatRequest(
                model=model.model,
                messages=messages,
                temperature=model.temperature,
                json_mode=True,
            )
            try:
                vote = await complete_structured(provider, request, VoteChoice)
            except (ProviderError, StructuredOutputError) as exc:
                self.events.emit(
                    ErrorEvent(message=f"{agent.name} could not vote: {exc}", agent_id=agent.id)
                )
                return agent, None
            option = match_choice(vote.choice, allowed)
            if option is not None:
                return agent, option.id
            messages = [
                *messages,
                Message("assistant", json.dumps({"choice": vote.choice})),
                retry_message(allowed),
            ]
        return agent, None

    async def _moderator_poll(self, text: str, index: int) -> None:
        """A poll the AI moderator chose to open after a round summary."""
        if not text.strip():
            return
        try:
            spec = parse_poll_spec(text)
        except ValueError as exc:
            self.events.emit(ErrorEvent(message=f"The moderator's poll could not be read: {exc}"))
            return
        self._moderator_polls += 1
        current = self.state.config.round_plan.rounds[index]
        await self._run_poll(spec, index, current, opened_by=MODERATOR_ID)

    # ------------------------------------------------------------- agent turns

    async def _agent_turn(
        self,
        agent: Persona,
        index: int,
        current: Round,
        turn: int,
        reply_to: ChatMessage | None = None,
        own_turn: bool = False,
    ) -> ChatMessage | None:
        """One agent speaks: a normal turn, or an answer to a message that @mentioned it
        (which can also be its own turn in the round)."""
        config = self.state.config
        message_id = new_id("msg")
        self.events.emit(
            TurnStarted(
                message_id=message_id,
                speaker_id=agent.id,
                round_index=index,
                reply_to=reply_to.id if reply_to else None,
            )
        )

        model = config.model_for(agent)
        reminders = self._reminders.pop(agent.id, [])
        request = ChatRequest(
            model=model.model,
            messages=build_agent_messages(
                self.state, agent, index, current, turn, reminders, reply_to, own_turn
            ),
            temperature=model.temperature,
        )
        try:
            spoken = await self._speak(message_id, agent.name, request, self._providers.get(model))
        except ProviderError as exc:
            self._turn_failed(agent, index, message_id, str(exc))
            return None

        text = spoken.text
        notes = spoken.captured.get("notes", "")
        if not notes:
            text, notes = split_trailing_notes(text)
        if not text:
            self._turn_failed(agent, index, message_id, f"{agent.name} returned an empty answer")
            return None

        self._failures = 0
        message = ChatMessage(
            id=message_id,
            speaker_id=agent.id,
            speaker_kind=SpeakerKind.AGENT,
            text=text,
            round_index=index,
            mentions=find_mentions(text, config.agents, exclude=agent.id),
            reply_to=reply_to.id if reply_to else None,
            usage=_token_usage(spoken.usage),
        )
        self.state.messages.append(message)
        self._last_speaker = agent.id
        self.events.emit(TurnCompleted(message=message))
        self._update_notebook(agent, notes, index)
        self._check_rules(agent, current, message, index)
        self._alliance_action(agent, spoken.captured.get("alliance", ""), current, index)
        if self._build_graph:
            self._graph_queue.put_nowait(message)  # mapped in the background
        return message

    async def _speak(
        self, message_id: str, name: str, request: ChatRequest, provider: Provider
    ) -> _Spoken:
        """Stream an answer as TokenDelta events, hiding <think> blocks, private sections,
        a leading "Name:" and trailing blank lines."""
        tags = TagFilter(capture=("notes", "plan", "alliance", "poll"))
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
        broken = check_rules(
            self.state.config, current, message.text, reply=message.reply_to is not None
        )
        if not broken:
            return
        for rule in broken:
            self.events.emit(RuleViolation(agent_id=agent.id, rule=rule, message_id=message.id))
        if self._human:
            return  # the human moderator decides whether to remind anyone
        self._reminders.setdefault(agent.id, []).extend(broken)
        if self._moderator:
            self._moderator_says(f"{agent.name}, a reminder: {' '.join(broken)}", index)

    def _alliance_action(self, agent: Persona, text: str, current: Round, index: int) -> None:
        """Apply an alliance the agent proposed, accepted, refused or left. If it can't be
        done, the agent is told why before its next turn."""
        if not text.strip() or not self.state.config.allow_alliances or current.blind:
            return
        try:
            action = parse_alliance_action(text)
            changes = apply_alliance_action(self.state, agent, action, index)
        except AllianceError as exc:
            self._reminders.setdefault(agent.id, []).append(
                f"Your alliance action could not be done: {exc}."
            )
            return
        except ValueError:
            self._reminders.setdefault(agent.id, []).append(
                "Your alliance line could not be read; use exactly the form shown."
            )
            return
        for change in changes:
            self.events.emit(change)

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
        self, messages: list[Message], round_index: int | None, purpose: str | None = None
    ) -> tuple[ChatMessage, _Spoken] | None:
        """The AI moderator speaks. A failure is reported but never stops the discussion."""
        config = self.state.config
        model = config.moderator_model()
        if model is None:
            return None
        message_id = new_id("msg")
        self.events.emit(
            TurnStarted(
                message_id=message_id,
                speaker_id=MODERATOR_ID,
                round_index=round_index,
                purpose=purpose,  # type: ignore[arg-type]
            )
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
        result = await self._moderator_turn(
            self._moderator.opening_messages(self.state), None, "opening"
        )
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
        """The AI moderator's conclusion, ending with a question for the audience."""
        assert self._moderator is not None
        result = await self._moderator_turn(
            self._moderator.report_messages(self.state), None, "conclusion"
        )
        if result is not None:
            self.state.report = result[0].text
            self.state.audience_question = audience_question(result[0].text)

    def _moderator_says(self, text: str, index: int | None) -> None:
        """A short moderator line that needs no model, such as a rule reminder."""
        self._post_instant(
            ChatMessage(
                speaker_id=MODERATOR_ID,
                speaker_kind=SpeakerKind.MODERATOR,
                text=text,
                round_index=index,
            )
        )

    def _post_instant(self, message: ChatMessage) -> None:
        """Post a message that is already complete, with the same events as a streamed one."""
        self.events.emit(
            TurnStarted(
                message_id=message.id,
                speaker_id=message.speaker_id,
                round_index=message.round_index,
            )
        )
        self.events.emit(TokenDelta(message_id=message.id, text=message.text))
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
