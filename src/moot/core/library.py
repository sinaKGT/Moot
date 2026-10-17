"""The starter persona library shown in the team builder."""

from __future__ import annotations

from moot.core.base import new_id
from moot.core.persona import Persona, Traits

_STARTERS: tuple[Persona, ...] = (
    Persona(
        id="lib_skeptic",
        name="Skeptic",
        color="#f97316",
        role="Critical thinker",
        motivation="Get to the truth by testing every claim.",
        thinking_style="Skeptical: questions assumptions and asks for evidence.",
        traits=Traits(persuadability=0.4, verbosity=0.4, alliance_openness=0.3),
    ),
    Persona(
        id="lib_pragmatist",
        name="Pragmatist",
        color="#22c55e",
        role="Practical advisor",
        motivation="Reach a decision that works in practice.",
        thinking_style="Practical: focuses on feasibility, cost and real-world constraints.",
        traits=Traits(persuadability=0.6, verbosity=0.4, alliance_openness=0.6),
    ),
    Persona(
        id="lib_domain_expert",
        name="Domain Expert",
        color="#3b82f6",
        role="Specialist",
        motivation="Keep the discussion grounded in expert knowledge.",
        expertise="Set this to the field the topic needs.",
        thinking_style="Analytical: explains how things work and corrects misconceptions.",
        traits=Traits(persuadability=0.5, verbosity=0.6, alliance_openness=0.4),
    ),
    Persona(
        id="lib_devils_advocate",
        name="Devil's Advocate",
        color="#ef4444",
        role="Contrarian",
        motivation="Win the argument by exposing weaknesses in the majority view.",
        thinking_style="Contrarian: deliberately argues the opposing side.",
        traits=Traits(persuadability=0.2, verbosity=0.5, alliance_openness=0.2),
    ),
    Persona(
        id="lib_innovator",
        name="Innovator",
        color="#e879f9",
        role="Creative thinker",
        motivation="Find a better option that nobody has considered yet.",
        thinking_style="Imaginative: proposes unconventional ideas and reframes the problem.",
        traits=Traits(persuadability=0.6, verbosity=0.5, alliance_openness=0.7),
    ),
)


def starter_personas() -> list[Persona]:
    """Fresh copies of the starter personas, each with its own new id, ready to add to a team."""
    return [persona.model_copy(update={"id": new_id("agt")}, deep=True) for persona in _STARTERS]
