"""Print a discussion to the terminal as it happens."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from typing import TextIO

from moot.core import (
    USER_ID,
    HumanModerator,
    Persona,
    Poll,
    PollResult,
    SessionConfig,
    SessionState,
    SpeakerKind,
)
from moot.core.events import (
    AllianceChanged,
    EngineNotice,
    ErrorEvent,
    Event,
    ModeratorDecision,
    NotebookUpdated,
    PollClosed,
    PollOpened,
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
from moot.memory import (
    alliance_mismatches,
    ended_line,
    names_of,
    pair_alignment,
    topic_sides,
)
from moot.memory import (
    argument_map as build_argument_map,
)

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
BAR_WIDTH = 20
MAX_OPTION_WIDTH = 32

END_TEXT = {
    "completed": "Discussion finished.",
    "stopped": "Discussion stopped.",
    "error": "Discussion ended because of errors.",
}


def enable_windows_ansi() -> None:
    """Turn on colour codes in the classic Windows console (newer terminals already have them)."""
    if os.name == "nt":
        os.system("")


class ConsoleRenderer:
    def __init__(
        self,
        config: SessionConfig,
        *,
        out: TextIO | None = None,
        color: bool | None = None,
        state: SessionState | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.out = out or sys.stdout
        self.color = self.out.isatty() if color is None else color
        if self.color:
            enable_windows_ansi()
        self._agents = {agent.id: agent for agent in config.agents}
        self._speakers: dict[str, str] = {}  # message id -> speaker id, to show who a reply answers
        self._polls: dict[str, Poll] = {}

    async def follow(self, events: AsyncIterator[Event]) -> None:
        async for event in events:
            self.handle(event)

    def handle(self, event: Event) -> None:
        match event:
            case SessionStarted():
                self._header()
            case RoundStarted(round_index=index):
                current = self.config.round_plan.rounds[index]
                title = f"── Round {index + 1} of {len(self.config.round_plan)}: {current.name} ──"
                self._write(
                    f"\n{self._style(BOLD, title)}\n{self._style(DIM, current.instruction)}\n"
                )
            case TurnStarted(
                message_id=message_id, speaker_id=speaker_id, reply_to=reply_to, purpose=purpose
            ):
                self._speakers[message_id] = speaker_id
                if purpose == "conclusion":
                    self._write(f"\n{self._style(BOLD, '── Conclusion ──')}\n")
                label = self._speaker(speaker_id)
                if reply_to in self._speakers:
                    label += f" → {self._name(self._speakers[reply_to])}"
                self._write(f"\n{label}: ")
            case TokenDelta(text=text):
                self._write(text)
            case TurnCompleted(message=message) if message.speaker_kind is SpeakerKind.SYSTEM:
                self._write(f"\n{self._style(DIM, '· ' + message.text)}\n")
            case TurnCompleted(message=message):
                usage = message.usage
                tokens = (
                    f"  [{usage.prompt_tokens} in / {usage.completion_tokens} out tokens]"
                    if usage
                    else ""
                )
                self._write(f"\n{self._style(DIM, tokens)}\n" if tokens else "\n")
            case ErrorEvent(agent_id=None, message=text):
                self._write(f"\n{self._style(RED, 'Error: ' + text)}\n")
            case NotebookUpdated(agent_id=agent_id) if self.state is not None:
                notebook = self.state.notebooks.get(agent_id)
                if notebook and notebook.verdict:
                    sure = round(notebook.confidence * 100)
                    self._write(
                        f"{self._style(DIM, f'  → {notebook.verdict.upper()}, {sure}% sure')}\n"
                    )
            case ModeratorDecision(next_speaker_id=speaker_id, reason=reason) if speaker_id:
                name = self._agents[speaker_id].name if speaker_id in self._agents else speaker_id
                because = f": {reason}" if reason else ""
                line = f"· {self.config.moderator_name} gives the floor to {name}{because}"
                self._write(f"\n{self._style(DIM, line)}\n")
            case WaitingForModerator(waiting_for=waiting_for, round_index=index):
                self._write(f"\n{self._style(BOLD, '» ' + self._your_move(waiting_for, index))}\n")
            case RuleViolation(agent_id=agent_id, rule=rule) if isinstance(
                self.config.moderator, HumanModerator
            ):
                name = self._agents[agent_id].name if agent_id in self._agents else agent_id
                self._write(f"{self._style(DIM, f'  ! Rule reminder for {name}: {rule}')}\n")
            case AllianceChanged() as change:
                self._write(f"\n· {self._describe_alliance(change)}\n")
            case PollOpened(poll=poll):
                self._polls[poll.id] = poll
                asker = self._name(poll.opened_by)
                options = ", ".join(option.label for option in poll.options)
                self._write(
                    f"\n{self._style(BOLD, f'── Poll: {poll.question} ──')}\n"
                    f"{self._style(DIM, f'Opened by {asker}. Options: {options}')}\n"
                )
            case VoteCast(voter_id=voter_id):
                self._write(f"{self._style(DIM, f'  ✓ {self._name(voter_id)} voted')}\n")
            case PollClosed(poll_id=poll_id, results=results):
                self._poll_results(poll_id, results)
            case EngineNotice(message=text):
                self._write(f"\n{self._style(DIM, '· ' + text)}\n")
            case SessionPaused():
                self._write(f"\n{self._style(DIM, '· Paused')}\n")
            case SessionResumed():
                self._write(f"\n{self._style(DIM, '· Resumed')}\n")
            case SessionEnded(reason=reason):
                self._write(f"\n{self._style(BOLD, END_TEXT[reason])}\n")
            case _:
                pass

    def _your_move(self, waiting_for: str, index: int | None) -> str:
        """What the human moderator can do now, in terminal commands."""
        rounds = self.config.round_plan.rounds
        if waiting_for == "start_round" and index is not None:
            upcoming = rounds[index]
            return (
                f"Your move, {self.config.moderator_name}: /next starts round {index + 1} "
                f"({upcoming.name}). You can speak, /poll or /remind first."
            )
        if waiting_for == "next_speaker":
            return (
                "Who speaks next? /floor Name gives someone the floor (/floor alone lets "
                "Moot choose). /end ends this round, /next moves on."
            )
        return "The round plan is done. Ask more questions or /poll, then /finish to end."

    def _poll_results(self, poll_id: str, results: list[PollResult]) -> None:
        """Draw the results like a real poll: a bar per option, with counts, percentages
        and who chose it."""
        poll = self._polls.get(poll_id)
        earlier = None
        if poll is not None and poll.previous_id and self.state is not None:
            earlier = next((p for p in self.state.polls if p.id == poll.previous_id), None)
        before = {r.label: r.votes for r in earlier.results()} if earlier else {}
        colors = {
            o.id: self._agents[o.agent_id].color
            for o in (poll.options if poll else [])
            if o.agent_id in self._agents
        }
        width = min(max((len(r.label) for r in results), default=0), MAX_OPTION_WIDTH)
        top = max((r.votes for r in results), default=0)
        self._write(f"{self._style(BOLD, 'Results')}\n")
        for result in results:
            label = result.label[:width].ljust(width)
            filled = round(BAR_WIDTH * result.percent / 100)
            bar = "█" * filled
            color = colors.get(result.option_id)
            bar = self._style(self._rgb(color), bar) if color else bar
            votes = f"{result.votes} vote{'s' if result.votes != 1 else ' '}"
            was = f" (was {before[result.label]})" if result.label in before else ""
            voters = ", ".join(self._name(v) for v in result.voters)
            chosen_by = f"  ← {voters}" if voters else ""
            line = (
                f"  {label}  {bar}{'░' * (BAR_WIDTH - filled)}  {votes}"
                f"{result.percent:5.0f}%{was}{chosen_by}"
            )
            self._write((self._style(BOLD, line) if result.votes == top and top else line) + "\n")

    def hint(self, text: str) -> None:
        self._write(f"{self._style(DIM, text)}\n")

    def render_argument_map(self, state: SessionState) -> None:
        """Print the claims, challenges, concessions and open questions from the graph."""
        sections = build_argument_map(state).sections()
        self._write(f"\n{self._style(BOLD, '── Argument map ──')}\n")
        if not sections:
            self._write(f"{self._style(DIM, 'No points were recorded.')}\n")
            return
        for title, items in sections:
            self._write(f"\n{self._style(BOLD, title)}\n")
            for item in items:
                self._write(f"  • {item}\n")

    def render_alliances(self, state: SessionState) -> None:
        """Print declared alliances, who backed whom in the argument map, and sides."""
        pairs = [p for p in pair_alignment(state) if p.interactions]
        if not state.alliances and not pairs:
            return
        self._write(f"\n{self._style(BOLD, '── Alliances ──')}\n")
        if state.alliances:
            self._write(f"\n{self._style(BOLD, 'Declared')}\n")
            for alliance in state.alliances:
                goal = f": {alliance.goal}" if alliance.goal else ""
                if alliance.status == "active":
                    since = alliance.formed_round
                    since_text = f" (since round {since + 1})" if since is not None else ""
                    line = f"{names_of(state, alliance.members)}{since_text}{goal}"
                elif alliance.status == "proposed":
                    line = (
                        f"{names_of(state, [alliance.proposed_by])} invited "
                        f"{names_of(state, alliance.invited)}{goal} (no answer)"
                    )
                elif alliance.formed_round is not None:
                    line = ended_line(state, alliance)
                else:
                    line = (
                        f"{names_of(state, [alliance.proposed_by])}'s invitation{goal} "
                        "was not taken up"
                    )
                self._write(f"  • {line}\n")
        if pairs:
            self._write(f"\n{self._style(BOLD, 'Who backed whom (from the argument map)')}\n")
            for pair in pairs:
                if pair.score >= 0.5:
                    verdict = "backed each other"
                elif pair.score <= -0.5:
                    verdict = "opposed each other"
                else:
                    verdict = "mixed"
                self._write(
                    f"  • {names_of(state, [pair.first, pair.second])}: {verdict} "
                    f"(support {pair.support:g}, challenge {pair.challenge:g})\n"
                )
        sides = {a: s for a, s in topic_sides(state).items() if s}
        if sides:
            listed = ", ".join(f"{state.config.speaker_label(a)} {s}" for a, s in sides.items())
            self._write(f"\n{self._style(BOLD, 'Sides on the topic')}\n  {listed}\n")
        for mismatch in alliance_mismatches(state):
            times = round(mismatch.challenges)
            self._write(
                f"\n  ! {state.config.speaker_label(mismatch.challenger)} challenged their ally "
                f"{state.config.speaker_label(mismatch.ally)} {times} "
                f"time{'s' if times != 1 else ''} since they allied\n"
            )

    def _describe_alliance(self, event: AllianceChanged) -> str:
        config = self.config
        alliance = event.alliance
        who = config.speaker_label(event.agent_id) if event.agent_id else ""
        reason = f": {event.reason}" if event.reason else ""

        def names(agent_ids: list[str]) -> str:
            labels = [config.speaker_label(a) for a in agent_ids if a != event.agent_id]
            return (
                ", ".join(labels[:-1]) + " and " + labels[-1]
                if len(labels) > 1
                else "".join(labels)
            )

        match event.change:
            case "proposed":
                goal = f": {alliance.goal}" if alliance.goal else ""
                return f"{who} proposes an alliance to {names(event.targets)}{goal}"
            case "accepted":
                return f"{who} joins the alliance with {names(alliance.members)}"
            case "refused":
                return f"{who} refuses the alliance with {names(alliance.members)}{reason}"
            case "left" if alliance.members:
                return f"{who} leaves the alliance with {names(alliance.members)}{reason}"
            case "left":
                return f"{who} withdraws the alliance proposal{reason}"
            case _:
                everyone = [config.speaker_label(a) for a in alliance.everyone]
                joined = (
                    ", ".join(everyone[:-1]) + " and " + everyone[-1]
                    if len(everyone) > 1
                    else "".join(everyone)
                )
                return f"The alliance of {joined} has ended"

    def _header(self) -> None:
        config = self.config
        people = ", ".join(self._agent_label(agent) for agent in config.agents)
        moderator = config.moderator_name
        if isinstance(config.moderator, HumanModerator):
            moderator += f" ({config.moderator.role}, you)"
        self._write(
            f"{self._style(BOLD, 'Topic:')} {config.topic}\n"
            f"{self._style(BOLD, 'Participants:')} {people}\n"
            f"{self._style(BOLD, 'Moderator:')} {moderator}\n"
        )

    def _agent_label(self, agent: Persona) -> str:
        name = self._style(self._rgb(agent.color), agent.name)
        return f"{name} ({agent.role})" if agent.role else name

    def _speaker(self, speaker_id: str) -> str:
        agent = self._agents.get(speaker_id)
        if agent is None:
            return self._style(BOLD, self._name(speaker_id))
        return self._style(BOLD + self._rgb(agent.color), agent.name)

    def _name(self, speaker_id: str) -> str:
        return "You" if speaker_id == USER_ID else self.config.speaker_label(speaker_id)

    def _style(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.color and text else text

    @staticmethod
    def _rgb(hex_color: str) -> str:
        red, green, blue = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
        return f"\x1b[38;2;{red};{green};{blue}m"

    def _write(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()
