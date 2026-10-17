"""A ready-made two-agent debate, used by `moot debate` and the watch-debate script."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
from typing import TextIO

from moot.console import ConsoleRenderer
from moot.core import (
    ModelRef,
    Persona,
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


def demo_config(topic: str, model: ModelRef, background: str = "") -> SessionConfig:
    """Ava (for), Ben (against) and Cleo (undecided, open to alliances), over an opening,
    a challenge and a closing round."""
    return SessionConfig(
        topic=topic,
        background=background,
        agents=[
            Persona(
                name="Ava",
                role="Advocate",
                color="#22d3ee",
                viewpoint="You argue in favour of the proposal in the topic.",
                motivation="Win the debate by building the strongest case for the proposal.",
                thinking_style="Constructive: focuses on benefits and practical solutions.",
                traits=Traits(persuadability=0.3, verbosity=0.4, alliance_openness=0.7),
            ),
            Persona(
                name="Ben",
                role="Critic",
                color="#f97316",
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
                color="#a78bfa",
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
            ]
        ),
        default_model=model,
    )


def listen_to_terminal(
    engine: DiscussionEngine, loop: asyncio.AbstractEventLoop, lines: TextIO | None = None
) -> threading.Thread:
    """Post each line typed in the terminal to the discussion, from a background thread."""

    def post(text: str) -> None:
        with contextlib.suppress(RuntimeError, ValueError):  # finished, or an empty line
            engine.say(text)

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

    With `interactive`, every line typed in the terminal is posted to the discussion.
    """
    engine = DiscussionEngine(state, providers=providers)
    renderer = ConsoleRenderer(state.config, out=out, color=color, state=state)
    if interactive:
        names = " or ".join(f"@{agent.name}" for agent in state.config.agents[:2])
        renderer.hint(
            f"Type a message and press Enter at any time to join in; write {names} "
            "to ask one of them directly."
        )
        listen_to_terminal(engine, asyncio.get_running_loop())
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
