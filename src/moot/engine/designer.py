"""The Team Designer: from a goal in your words to a panel, a format and (for decisions)
the options, ready to review in the team builder.

One structured call proposes the panel; an optional second call, the balance check, reviews
it for views that are too alike or a side that is missing and may revise it. Whatever the
model returns is tidied here (unique names, traits in range, a known format, clean options),
so the proposal always fits the team builder.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, field_validator

from moot.core.persona import Persona, Traits
from moot.core.round_plan import TEMPLATES
from moot.core.session import MAX_OPTION_CHARS, MAX_OPTIONS
from moot.providers import (
    ChatRequest,
    Message,
    Provider,
    StructuredOutputError,
    complete_structured,
)

MIN_AGENTS = 2
MAX_AGENTS = 8
DEFAULT_FORMAT = "classic_debate"
MAX_GOAL_CHARS = 4000
# names an agent can't have: the moderator, you, and the @all / @everyone mentions
RESERVED_NAMES = ("Moderator", "You", "All", "Everyone")

FORMAT_HINTS = {
    "classic_debate": (
        "Classic debate: for a question with sides (opening, challenge, rebuttal, closing, vote)"
    ),
    "brainstorm": "Brainstorm: for generating and building on ideas",
    "quick_take": "Quick take: one round of short views and a summary",
    "decision": (
        "Decision: for choosing between options (options, criteria, weigh, pre-mortem, decide)"
    ),
}

DESIGNER_SYSTEM = """You design panels for Moot, where AI agents with different viewpoints discuss \
a topic in rounds, led by a moderator.

From the user's goal, propose:
- "topic": the question the panel will discuss, one clear sentence (keep the user's language).
- "format": one of {formats}.
- "options": only for a decision, the 2 to {max_options} choices on the table: the ones the user \
names, else realistic ones. Otherwise an empty list.
- "agents": {count} participants whose views clearly differ and together cover the main positions \
people really hold. For each: "name" (a short, made-up first name; never a real person), \
"role" (a few words), "viewpoint" (what they believe and argue for, written to them: \
"You argue that ..."), "motivation" (what they want from the discussion), "expertise" \
(what they know), "thinking_style" (how they argue), and three numbers from 0 to 1: \
"persuadability" (0 holds firm, 1 easily persuaded), "verbosity" (0 concise, 1 detailed) and \
"alliance_openness" (0 lone wolf, 1 seeks allies). Vary the styles and numbers: not everyone \
should be stubborn, or agreeable.
- "rationale": one or two sentences on why this panel fits the goal.

Represent every view fairly and at its strongest; no straw men. Don't state facts as settled \
that are not, and don't invent statistics or sources.

Reply with only a JSON object:
{{"topic": "...", "format": "...", "options": [], "agents": [{{"name": "...", "role": "...", \
"viewpoint": "...", "motivation": "...", "expertise": "...", "thinking_style": "...", \
"persuadability": 0.5, "verbosity": 0.4, "alliance_openness": 0.5}}], "rationale": "..."}}"""

BALANCE_SYSTEM = """You review discussion panels for Moot before they start. Check that the \
participants' views are clearly distinct (no two argue the same thing in other words), that the \
main positions on the topic are represented, and that each view is described fairly, at its \
strongest. If the panel is fine, say so. If not, give a revised list of participants (same JSON \
shape, same number unless one view is missing or two are the same) that fixes it, keeping what \
works.

Reply with only a JSON object:
{"balanced": true, "notes": ["one short note per issue found, or one line saying it is balanced"], \
"agents": null}
or, with a revised panel, "balanced": false and "agents": [ ... ]."""


class _Lenient(BaseModel):
    model_config = {"extra": "ignore"}


class ProposedAgent(_Lenient):
    name: str = ""
    role: str = ""
    viewpoint: str = ""
    motivation: str = ""
    expertise: str = ""
    thinking_style: str = ""
    persuadability: float = 0.5
    verbosity: float = 0.5
    alliance_openness: float = 0.5

    @field_validator("persuadability", "verbosity", "alliance_openness", mode="before")
    @classmethod
    def _number(cls, value: object) -> float:
        try:
            return min(1.0, max(0.0, float(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5


class _Draft(_Lenient):
    topic: str = ""
    format: str = ""
    options: list[str] = []
    agents: list[ProposedAgent] = []
    rationale: str = ""


class _Review(_Lenient):
    balanced: bool = True
    notes: list[str] = []
    agents: list[ProposedAgent] | None = None


class TeamProposal(BaseModel):
    """What the designer proposes, tidied so it fits the team builder."""

    topic: str
    format: str = Field(description="a round-plan template id")
    options: list[str] = Field(default_factory=list, description="only for decisions")
    agents: list[Persona]
    rationale: str = ""
    balance_notes: list[str] = Field(
        default_factory=list, description="what the balance check found (empty if skipped)"
    )
    revised: bool = Field(default=False, description="the balance check changed the panel")


class DesignError(RuntimeError):
    """The designer's answer could not be turned into a panel."""


def _clean_text(text: str, limit: int) -> str:
    return " ".join(str(text).split())[:limit]


def _clean_name(name: str) -> str:
    name = re.sub(r"[@\n\r]", "", " ".join(str(name).split()))
    return name[:40].strip()


def clean_agents(
    proposed: list[ProposedAgent],
    *,
    count: int | None = None,
    taken: tuple[str, ...] = RESERVED_NAMES,
) -> list[Persona]:
    """Personas from the proposal: named, unique, traits in range, at most MAX_AGENTS (or the
    requested count). Agents without a name or viewpoint are dropped."""
    limit = min(count or MAX_AGENTS, MAX_AGENTS)
    used = {name.casefold() for name in taken}
    agents: list[Persona] = []
    for item in proposed:
        if len(agents) == limit:
            break
        base = _clean_name(item.name)
        if not base or not item.viewpoint.strip():
            continue
        name, n = base, 2
        while name.casefold() in used:
            name = f"{base[:36]} {n}"
            n += 1
        used.add(name.casefold())
        agents.append(
            Persona(
                name=name,
                role=_clean_text(item.role, 80),
                viewpoint=_clean_text(item.viewpoint, 1000),
                motivation=_clean_text(item.motivation, 400),
                expertise=_clean_text(item.expertise, 400),
                thinking_style=_clean_text(item.thinking_style, 400),
                traits=Traits(
                    persuadability=item.persuadability,
                    verbosity=item.verbosity,
                    alliance_openness=item.alliance_openness,
                ),
            )
        )
    return agents


def clean_options(options: list[str]) -> list[str]:
    cleaned: list[str] = []
    for option in options:
        text = _clean_text(option, MAX_OPTION_CHARS).strip(" .")
        if text and text.casefold() not in {o.casefold() for o in cleaned}:
            cleaned.append(text)
    return cleaned[:MAX_OPTIONS]


class TeamDesigner:
    """Turns a goal into a TeamProposal with one model, or two calls with the balance check."""

    def __init__(self, *, temperature: float = 0.7) -> None:
        self.temperature = temperature

    def design_messages(self, goal: str, count: int | None) -> list[Message]:
        formats = "; ".join(
            f'"{key}" ({hint})' for key, hint in FORMAT_HINTS.items() if key in TEMPLATES
        )
        system = DESIGNER_SYSTEM.format(
            formats=formats,
            max_options=MAX_OPTIONS,
            count=f"exactly {count}" if count else f"{MIN_AGENTS + 1} to 5 (as the goal needs)",
        )
        return [
            Message("system", system),
            Message("user", f"Goal:\n{goal.strip()[:MAX_GOAL_CHARS]}"),
        ]

    def balance_messages(self, topic: str, agents: list[Persona]) -> list[Message]:
        panel = [
            {
                "name": a.name,
                "role": a.role,
                "viewpoint": a.viewpoint,
                "motivation": a.motivation,
                "expertise": a.expertise,
                "thinking_style": a.thinking_style,
                "persuadability": a.traits.persuadability,
                "verbosity": a.traits.verbosity,
                "alliance_openness": a.traits.alliance_openness,
            }
            for a in agents
        ]
        user = f"Topic: {topic}\n\nPanel:\n{json.dumps(panel, ensure_ascii=False, indent=1)}"
        return [Message("system", BALANCE_SYSTEM), Message("user", user)]

    async def design(
        self,
        goal: str,
        provider: Provider,
        model: str,
        *,
        agents: int | None = None,
        balance_check: bool = True,
    ) -> TeamProposal:
        if not goal.strip():
            raise DesignError("Describe the goal first.")
        if agents is not None and not MIN_AGENTS <= agents <= MAX_AGENTS:
            raise DesignError(f"Ask for {MIN_AGENTS} to {MAX_AGENTS} agents.")
        try:
            draft = await complete_structured(
                provider,
                ChatRequest(
                    model=model,
                    messages=self.design_messages(goal, agents),
                    temperature=self.temperature,
                ),
                _Draft,
            )
        except StructuredOutputError as exc:
            raise DesignError(f"The designer's answer couldn't be used: {exc}") from exc

        panel = clean_agents(draft.agents, count=agents)
        if len(panel) < MIN_AGENTS:
            raise DesignError("The designer proposed fewer than two usable agents. Try again.")
        options = clean_options(draft.options)
        if len(options) < 2:
            options = []
        chosen = draft.format.strip().lower().replace(" ", "_")
        if chosen not in TEMPLATES:
            chosen = "decision" if options else DEFAULT_FORMAT
        if chosen == "decision" and not options:
            chosen = DEFAULT_FORMAT  # a decision needs options; without them it is a debate
        topic = _clean_text(draft.topic, 300) or _clean_text(goal, 300)

        proposal = TeamProposal(
            topic=topic,
            format=chosen,
            options=options,
            agents=panel,
            rationale=_clean_text(draft.rationale, 600),
        )
        if balance_check:
            await self._check_balance(proposal, provider, model, agents)
        return proposal

    async def _check_balance(
        self, proposal: TeamProposal, provider: Provider, model: str, count: int | None
    ) -> None:
        """The second pass. A failure here keeps the first panel, with a note."""
        try:
            review = await complete_structured(
                provider,
                ChatRequest(
                    model=model,
                    messages=self.balance_messages(proposal.topic, proposal.agents),
                    temperature=0.2,
                ),
                _Review,
            )
        except StructuredOutputError:
            proposal.balance_notes = [
                "The balance check didn't give a usable answer; check the views yourself."
            ]
            return
        proposal.balance_notes = [_clean_text(n, 300) for n in review.notes if str(n).strip()][:6]
        if not review.balanced and review.agents:
            revised = clean_agents(review.agents, count=count)
            if len(revised) >= MIN_AGENTS:
                proposal.agents = revised
                proposal.revised = True
        if not proposal.balance_notes:
            proposal.balance_notes = (
                ["The balance check revised the panel."]
                if proposal.revised
                else ["The views are distinct and fairly represented."]
            )
