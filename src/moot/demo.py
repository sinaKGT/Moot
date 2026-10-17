"""A ready-made two-agent debate, used by `moot debate` and the watch-debate script."""

from __future__ import annotations

import asyncio
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
    Traits,
)
from moot.core.events import SessionEnded
from moot.engine import DiscussionEngine, ProviderPool

DEMO_TOPIC = "Should cities ban private cars from their centres?"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"


def demo_config(topic: str, model: ModelRef) -> SessionConfig:
    """Two agents on opposite sides, an opening round and a challenge round."""
    return SessionConfig(
        topic=topic,
        agents=[
            Persona(
                name="Ava",
                role="Advocate",
                color="#22d3ee",
                viewpoint="You argue in favour of the proposal in the topic.",
                motivation="Win the debate by building the strongest case for the proposal.",
                thinking_style="Constructive: focuses on benefits and practical solutions.",
                traits=Traits(persuadability=0.3, verbosity=0.4, alliance_openness=0.5),
            ),
            Persona(
                name="Ben",
                role="Critic",
                color="#f97316",
                viewpoint="You argue against the proposal in the topic.",
                motivation="Win the debate by exposing the weaknesses of the proposal.",
                thinking_style="Skeptical: questions assumptions and asks for evidence.",
                traits=Traits(persuadability=0.3, verbosity=0.4, alliance_openness=0.3),
            ),
        ],
        rules=SessionRules(
            presets=[RulePreset.SHORT_ANSWERS, RulePreset.EVIDENCE_REQUIRED],
            max_words_per_turn=120,
        ),
        round_plan=RoundPlan(
            rounds=[Round(type=RoundType.OPENING), Round(type=RoundType.CHALLENGE)]
        ),
        default_model=model,
    )


async def run_in_console(
    state: SessionState,
    *,
    providers: ProviderPool | None = None,
    out: TextIO | None = None,
    color: bool | None = None,
) -> str:
    """Run a session while printing it; return how it ended ("completed", "stopped", "error")."""
    engine = DiscussionEngine(state, providers=providers)
    renderer = ConsoleRenderer(state.config, out=out, color=color)
    follower = asyncio.create_task(renderer.follow(engine.events.subscribe()))
    try:
        await engine.run()
    finally:
        engine.events.close()
        await follower
    ended = [e for e in engine.events.history if isinstance(e, SessionEnded)]
    return ended[-1].reason if ended else "error"
