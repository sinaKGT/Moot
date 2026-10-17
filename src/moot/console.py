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
    SessionConfig,
    SessionState,
    SpeakerKind,
)
from moot.core.events import (
    EngineNotice,
    ErrorEvent,
    Event,
    ModeratorDecision,
    NotebookUpdated,
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
from moot.memory import argument_map

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"

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
            case TurnStarted(message_id=message_id, speaker_id=speaker_id, reply_to=reply_to):
                self._speakers[message_id] = speaker_id
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
                line = f"· {self.config.moderator_name} gives the floor to {name}: {reason}"
                self._write(f"\n{self._style(DIM, line)}\n")
            case RuleViolation(agent_id=agent_id, rule=rule) if isinstance(
                self.config.moderator, HumanModerator
            ):
                name = self._agents[agent_id].name if agent_id in self._agents else agent_id
                self._write(f"{self._style(DIM, f'  ! Rule reminder for {name}: {rule}')}\n")
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

    def hint(self, text: str) -> None:
        self._write(f"{self._style(DIM, text)}\n")

    def render_argument_map(self, state: SessionState) -> None:
        """Print the claims, challenges, concessions and open questions from the graph."""
        sections = argument_map(state).sections()
        self._write(f"\n{self._style(BOLD, '── Argument map ──')}\n")
        if not sections:
            self._write(f"{self._style(DIM, 'No points were recorded.')}\n")
            return
        for title, items in sections:
            self._write(f"\n{self._style(BOLD, title)}\n")
            for item in items:
                self._write(f"  • {item}\n")

    def _header(self) -> None:
        config = self.config
        people = ", ".join(self._agent_label(agent) for agent in config.agents)
        self._write(
            f"{self._style(BOLD, 'Topic:')} {config.topic}\n"
            f"{self._style(BOLD, 'Participants:')} {people}\n"
            f"{self._style(BOLD, 'Moderator:')} {config.moderator_name}\n"
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
