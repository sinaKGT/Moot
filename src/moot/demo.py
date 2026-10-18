"""A ready-made two-agent debate, used by `moot debate` and the watch-debate script."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
from collections.abc import Callable
from typing import TextIO

from moot.console import ConsoleRenderer
from moot.core import (
    HumanModerator,
    ModelRef,
    Persona,
    PollSpec,
    Round,
    RoundPlan,
    RoundType,
    RulePreset,
    SessionConfig,
    SessionRules,
    SessionState,
    SpeakingOrder,
    Traits,
)
from moot.core.events import SessionEnded
from moot.engine import DiscussionEngine, ProviderPool

DEMO_TOPIC = "Should cities ban private cars from their centres?"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"


def demo_config(
    topic: str,
    model: ModelRef,
    background: str = "",
    moderator: HumanModerator | None = None,
) -> SessionConfig:
    """Ava (for), Ben (against) and Cleo (undecided, open to alliances), over an opening,
    a challenge and a closing round, then a vote on who made the strongest case.

    With a human moderator, they choose who speaks in the challenge round and start each
    round; the model also builds the argument map."""
    extra: dict = {}
    if moderator is not None:
        extra = {"moderator": moderator, "graph_builder_model": model}
    return SessionConfig(
        topic=topic,
        background=background,
        agents=[
            Persona(
                name="Ava",
                role="Advocate",
                color="#60a5fa",
                viewpoint="You argue in favour of the proposal in the topic.",
                motivation="Win the debate by building the strongest case for the proposal.",
                thinking_style="Constructive: focuses on benefits and practical solutions.",
                traits=Traits(persuadability=0.3, verbosity=0.4, alliance_openness=0.7),
            ),
            Persona(
                name="Ben",
                role="Critic",
                color="#fb7185",
                viewpoint="You argue against the proposal in the topic.",
                motivation="Win the debate by exposing the weaknesses of the proposal.",
                thinking_style=(
                    "Critical: finds concrete flaws, gives counter-examples and offers his own "
                    "alternative view."
                ),
                traits=Traits(persuadability=0.3, verbosity=0.4, alliance_openness=0.5),
            ),
            Persona(
                name="Cleo",
                role="Pragmatist",
                color="#a3e635",
                viewpoint=(
                    "You start undecided and side with whichever arguments hold up best in "
                    "practice."
                ),
                motivation=(
                    "Reach the most workable answer, and team up with whoever argues for it."
                ),
                thinking_style="Practical: weighs costs, benefits and real-world examples.",
                traits=Traits(persuadability=0.7, verbosity=0.4, alliance_openness=0.8),
            ),
        ],
        rules=SessionRules(
            presets=[RulePreset.ANSWER_FIRST, RulePreset.ONE_POINT, RulePreset.SHORT_ANSWERS],
            max_words_per_turn=120,
        ),
        round_plan=RoundPlan(
            rounds=[
                Round(type=RoundType.OPENING),
                Round(
                    type=RoundType.CHALLENGE,
                    speaking_order=SpeakingOrder.MODERATOR,
                    mentions_allowed=True,
                ),
                Round(type=RoundType.CLOSING),
                Round(type=RoundType.VOTE),
            ]
        ),
        default_model=model,
        **extra,
    )


TERMINAL_HELP = (
    "Commands: /poll (who made the strongest case?), /poll Question | option | option, "
    "/pause, /resume."
)
MODERATOR_HELP = (
    "As the moderator: /next (start or move to the next round), /floor Name (give someone "
    "the floor), /end (end this round), /remind Name text, /finish (end the discussion)."
)
DEFAULT_POLL_OPTIONS = ("Yes", "No", "Not sure")


def terminal_help(config: SessionConfig) -> str:
    if isinstance(config.moderator, HumanModerator):
        return f"{TERMINAL_HELP} {MODERATOR_HELP}"
    return TERMINAL_HELP


def _split_name(config: SessionConfig, text: str) -> tuple[str, str]:
    """'Domain Expert keep it short' -> ('Domain Expert', 'keep it short')."""
    cleaned = text.strip().removeprefix("@")
    for agent in sorted(config.agents, key=lambda a: len(a.name), reverse=True):
        name = agent.name
        if cleaned.casefold().startswith(name.casefold()) and (
            len(cleaned) == len(name) or not cleaned[len(name)].isalnum()
        ):
            return name, cleaned[len(name) :].strip(" ,:-")
    raise ValueError("start with a participant's name, e.g. /remind Ben keep it short")


def terminal_command(engine: DiscussionEngine, text: str) -> str | None:
    """Act on one typed line: a command, or a message to the discussion.
    Returns something to show the user, if anything."""
    text = text.strip()
    if not text:
        return None
    if not text.startswith("/"):
        engine.say(text)
        return None
    command, _, rest = text.partition(" ")
    match command.lower():
        case "/poll":
            question, *options = (part.strip() for part in rest.split("|"))
            if not question and not options:
                engine.open_poll()
            else:
                engine.open_poll(
                    PollSpec(question=question, options=options or list(DEFAULT_POLL_OPTIONS))
                )
            return "The poll will start after the current turn."
        case "/pause":
            engine.pause()
        case "/resume":
            engine.resume()
        case "/next":
            engine.next_round()
        case "/end":
            engine.end_round()
        case "/finish":
            engine.finish()
        case "/floor":
            engine.give_floor(rest.strip() or None)
        case "/remind":
            name, reminder = _split_name(engine.state.config, rest)
            engine.remind(name, reminder)
        case _:
            return f"Unknown command {command}. {terminal_help(engine.state.config)}"
    return None


def listen_to_terminal(
    engine: DiscussionEngine,
    loop: asyncio.AbstractEventLoop,
    lines: TextIO | None = None,
    show: Callable[[str], None] | None = None,
) -> threading.Thread:
    """Act on each line typed in the terminal, from a background thread."""

    def post(text: str) -> None:
        try:
            reply = terminal_command(engine, text)
        except RuntimeError:  # the discussion has finished
            return
        except ValueError as exc:  # e.g. a poll with fewer than two options
            reply = f"That didn't work: {exc}"
        if reply and show is not None:
            show(reply)

    def read() -> None:
        for line in lines or sys.stdin:
            if line.strip():
                with contextlib.suppress(RuntimeError):  # the event loop has closed
                    loop.call_soon_threadsafe(post, line)

    thread = threading.Thread(target=read, name="moot-terminal-input", daemon=True)
    thread.start()
    return thread


async def run_in_console(
    state: SessionState,
    *,
    providers: ProviderPool | None = None,
    out: TextIO | None = None,
    color: bool | None = None,
    show_map: bool = True,
    interactive: bool = False,
) -> str:
    """Run a session while printing it; return how it ended ("completed", "stopped", "error").

    With `interactive`, every line typed in the terminal is posted to the discussion. A
    human-moderated session needs it, since the moderator steers it by typing.
    """
    if isinstance(state.config.moderator, HumanModerator) and not interactive:
        raise ValueError("a discussion you moderate needs a terminal to type in")
    engine = DiscussionEngine(state, providers=providers)
    renderer = ConsoleRenderer(state.config, out=out, color=color, state=state)
    if interactive:
        names = " or ".join(f"@{agent.name}" for agent in state.config.agents[:2])
        renderer.hint(
            f"Type a message and press Enter at any time to join in; write {names} "
            f"to ask one of them directly (@all asks everyone). {terminal_help(state.config)}"
        )
        listen_to_terminal(engine, asyncio.get_running_loop(), show=renderer.hint)
    follower = asyncio.create_task(renderer.follow(engine.events.subscribe()))
    try:
        await engine.run()
    finally:
        engine.events.close()
        await follower
    if show_map and state.messages:
        renderer.render_argument_map(state)
        renderer.render_alliances(state)
    ended = [e for e in engine.events.history if isinstance(e, SessionEnded)]
    return ended[-1].reason if ended else "error"
