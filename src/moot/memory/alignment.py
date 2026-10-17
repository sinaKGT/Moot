"""Who backs whom: alignment detected from the argument graph, and declared alliances.

Detected alignment comes from links between agents' points: supporting, refining or
conceding to someone's point counts for them, challenging it counts against them. It can
be computed up to any round, for the alliance monitor's round slider. Declared alliances
whose members keep challenging each other are flagged.
"""

from __future__ import annotations

import contextlib
from itertools import combinations
from typing import Literal

from pydantic import Field

from moot.core import (
    Alliance,
    AllianceError,
    AllianceStatus,
    EdgeType,
    MootModel,
    NodeType,
    Persona,
    SessionState,
)

LINK_WEIGHTS: dict[EdgeType, tuple[float, float]] = {  # (support, challenge)
    EdgeType.SUPPORTS: (1.0, 0.0),
    EdgeType.REFINES: (0.5, 0.0),
    EdgeType.CONCEDES_TO: (0.5, 0.0),
    EdgeType.CHALLENGES: (0.0, 1.0),
}
MAX_ENDED_ALLIANCES_SHOWN = 2

Side = Literal["for", "against", "mixed"]


class Relation(MootModel):
    """How one agent has treated another's points (directed)."""

    source: str
    target: str
    support: float = 0.0
    challenge: float = 0.0


class PairAlignment(MootModel):
    """How two agents stand towards each other, counting links in both directions."""

    first: str
    second: str
    support: float = 0.0
    challenge: float = 0.0
    allied: bool = Field(default=False, description="in the same active declared alliance")

    @property
    def interactions(self) -> float:
        return self.support + self.challenge

    @property
    def score(self) -> float:
        """From -1 (only opposing each other) to 1 (only supporting each other)."""
        if not self.interactions:
            return 0.0
        return (self.support - self.challenge) / self.interactions


class Mismatch(MootModel):
    """A declared ally who has challenged another member more than supported them."""

    alliance_id: str
    challenger: str
    ally: str
    challenges: float
    supports: float


class AllianceMonitor(MootModel):
    """Everything the alliance monitor shows, up to a round (or the whole session)."""

    up_to_round: int | None = None
    relations: list[Relation] = Field(default_factory=list)
    pairs: list[PairAlignment] = Field(default_factory=list)
    sides: dict[str, Side | None] = Field(default_factory=dict)
    alliances: list[Alliance] = Field(default_factory=list)
    mismatches: list[Mismatch] = Field(default_factory=list)


def _counted(round_index: int | None, up_to_round: int | None, since: int | None = None) -> bool:
    if up_to_round is not None and (round_index is None or round_index > up_to_round):
        return False
    return since is None or (round_index is not None and round_index >= since)


def relations(
    state: SessionState, up_to_round: int | None = None, since_round: int | None = None
) -> dict[tuple[str, str], Relation]:
    """Directed relations between agents, from the links between their points."""
    graph = state.graph
    agent_ids = {agent.id for agent in state.config.agents}
    found: dict[tuple[str, str], Relation] = {}
    for edge in graph.edges:
        weights = LINK_WEIGHTS.get(edge.type)
        if weights is None:
            continue
        source, target = graph.node(edge.source), graph.node(edge.target)
        a, b = source.speaker_id, target.speaker_id
        if a not in agent_ids or b not in agent_ids or a == b:
            continue
        if not _counted(source.round_index, up_to_round, since_round):
            continue
        relation = found.setdefault((a, b), Relation(source=a, target=b))  # type: ignore[arg-type]
        relation.support += weights[0]
        relation.challenge += weights[1]
    return found


def pair_alignment(state: SessionState, up_to_round: int | None = None) -> list[PairAlignment]:
    """Every pair of agents, in panel order."""
    found = relations(state, up_to_round)
    allied = {
        frozenset(pair)
        for alliance in _alliances_at(state, up_to_round)
        if alliance.status is AllianceStatus.ACTIVE
        for pair in combinations(alliance.members, 2)
    }
    pairs = []
    for first, second in combinations([a.id for a in state.config.agents], 2):
        pair = PairAlignment(
            first=first, second=second, allied=frozenset((first, second)) in allied
        )
        for key in ((first, second), (second, first)):
            if key in found:
                pair.support += found[key].support
                pair.challenge += found[key].challenge
        pairs.append(pair)
    return pairs


def topic_sides(state: SessionState, up_to_round: int | None = None) -> dict[str, Side | None]:
    """Each agent's side on the topic, from its points that support or challenge it."""
    graph = state.graph
    topic_ids = {node.id for node in graph.find(node_type=NodeType.TOPIC)}
    counts = {agent.id: [0, 0] for agent in state.config.agents}
    for edge in graph.edges:
        if edge.target not in topic_ids or edge.type not in (
            EdgeType.SUPPORTS,
            EdgeType.CHALLENGES,
        ):
            continue
        source = graph.node(edge.source)
        if source.speaker_id in counts and _counted(source.round_index, up_to_round):
            counts[source.speaker_id][edge.type is EdgeType.CHALLENGES] += 1
    sides: dict[str, Side | None] = {}
    for agent_id, (pro, contra) in counts.items():
        if pro > contra:
            sides[agent_id] = "for"
        elif contra > pro:
            sides[agent_id] = "against"
        else:
            sides[agent_id] = "mixed" if pro else None
    return sides


def alliance_mismatches(state: SessionState) -> list[Mismatch]:
    """Members of an active alliance who challenged an ally more than they supported them
    since the alliance was formed."""
    found = []
    for alliance in state.active_alliances():
        since = alliance.formed_round
        links = relations(state, since_round=since)
        for challenger, ally in combinations(alliance.members, 2):
            for a, b in ((challenger, ally), (ally, challenger)):
                relation = links.get((a, b))
                if relation and relation.challenge > relation.support:
                    found.append(
                        Mismatch(
                            alliance_id=alliance.id,
                            challenger=a,
                            ally=b,
                            challenges=relation.challenge,
                            supports=relation.support,
                        )
                    )
    return found


def alliance_monitor(state: SessionState, up_to_round: int | None = None) -> AllianceMonitor:
    return AllianceMonitor(
        up_to_round=up_to_round,
        relations=list(relations(state, up_to_round).values()),
        pairs=pair_alignment(state, up_to_round),
        sides=topic_sides(state, up_to_round),
        alliances=_alliances_at(state, up_to_round),
        mismatches=alliance_mismatches(state) if up_to_round is None else [],
    )


def _alliances_at(state: SessionState, up_to_round: int | None) -> list[Alliance]:
    """The alliances as they were at the end of a round, replayed from their histories."""
    if up_to_round is None:
        return list(state.alliances)
    rebuilt = []
    for alliance in state.alliances:
        steps = [
            s
            for s in alliance.history
            if s.round_index is not None and s.round_index <= up_to_round
        ]
        if not steps or steps[0].change != "proposed":
            continue
        first = steps[0]
        copy = Alliance.propose(
            alliance.proposed_by,
            first.targets,
            name=alliance.name,
            goal=alliance.goal,
            round_index=first.round_index,
        )
        copy.id = alliance.id
        for step in steps[1:]:
            agent, at = step.agent_id or "", step.round_index
            with contextlib.suppress(AllianceError):
                if step.change == "proposed":
                    copy.invite(agent, step.targets, round_index=at)
                elif step.change == "accepted":
                    copy.accept(agent, round_index=at)
                elif step.change == "refused":
                    copy.refuse(agent, round_index=at, reason=step.reason)
                elif step.change == "left":
                    copy.leave(agent, round_index=at, reason=step.reason)
        rebuilt.append(copy)
    return rebuilt


# ------------------------------------------------------------------ wording


def names_of(state: SessionState, agent_ids: list[str]) -> str:
    names = [state.config.speaker_label(a) for a in agent_ids]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _goal(alliance: Alliance) -> str:
    return f": {alliance.goal}" if alliance.goal else ""


def _since(alliance: Alliance) -> str:
    formed = alliance.formed_round
    return f" (since round {formed + 1})" if formed is not None else ""


def invitation_lines(state: SessionState, agent: Persona) -> list[str]:
    """Alliance invitations waiting for this agent's answer."""
    if not state.config.allow_alliances:
        return []
    lines = []
    for alliance in state.alliances:
        if alliance.is_open and agent.id in alliance.invited:
            inviter = next(
                (
                    s.agent_id
                    for s in reversed(alliance.history)
                    if s.change == "proposed" and agent.id in s.targets and s.agent_id
                ),
                alliance.proposed_by,
            )
            members = [m for m in alliance.members if m != inviter]
            others = f" (with {names_of(state, members)})" if members else ""
            lines.append(
                f"- {state.config.speaker_label(inviter)} invited you to an alliance{others}"
                f"{_goal(alliance)}. Accept or refuse it in this answer."
            )
    return lines


def alliance_lines(state: SessionState, agent: Persona | None = None) -> list[str]:
    """The public picture of alliances, from one agent's point of view (or neutral)."""
    if not state.config.allow_alliances or not state.alliances:
        return []
    me = agent.id if agent else None
    lines = []
    for alliance in state.active_alliances():
        if me in alliance.members:
            others = [m for m in alliance.members if m != me]
            lines.append(
                f"- You are allied with {names_of(state, others)}{_since(alliance)}"
                f"{_goal(alliance)}"
            )
        else:
            lines.append(
                f"- {names_of(state, alliance.members)} are allied{_since(alliance)}"
                f"{_goal(alliance)}"
            )
        if alliance.invited and (me is None or me not in alliance.invited):
            lines.append(f"  Invited, no answer yet: {names_of(state, alliance.invited)}")
    for alliance in state.alliances:
        if alliance.status is AllianceStatus.PROPOSED and alliance.proposed_by == me:
            lines.append(
                f"- You invited {names_of(state, alliance.invited)} to an alliance; no answer yet"
            )
    for mismatch in alliance_mismatches(state):
        challenger = state.config.speaker_label(mismatch.challenger)
        ally = state.config.speaker_label(mismatch.ally)
        times = round(mismatch.challenges)
        lines.append(
            f"- {challenger} has challenged their ally {ally} {times} "
            f"time{'s' if times != 1 else ''} since they allied"
        )
    ended = [
        a
        for a in state.alliances
        if a.status is AllianceStatus.DISSOLVED and a.formed_round is not None
    ]
    for alliance in ended[-MAX_ENDED_ALLIANCES_SHOWN:]:
        lines.append(f"- {ended_line(state, alliance)}")
    return lines


def ended_line(state: SessionState, alliance: Alliance) -> str:
    leaving = next((s for s in reversed(alliance.history) if s.change == "left"), None)
    who = names_of(state, alliance.everyone)
    when = alliance.ended_round
    at = f" in round {when + 1}" if when is not None else ""
    if leaving is None or leaving.agent_id is None:
        return f"The alliance of {who} ended{at}"
    reason = f": {leaving.reason}" if leaving.reason else ""
    leaver = state.config.speaker_label(leaving.agent_id)
    return f"The alliance of {who} ended{at} when {leaver} left{reason}"
