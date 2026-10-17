"""A readable summary of the argument graph: positions, claims, challenges, concessions and
open questions."""

from __future__ import annotations

from dataclasses import dataclass, field

from moot.core import EdgeType, Node, NodeType, SessionState

MAX_LINE_TEXT = 140


def shorten(text: str, limit: int = MAX_LINE_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


@dataclass
class ArgumentMap:
    positions: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    challenges: list[str] = field(default_factory=list)
    concessions: list[str] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)

    def sections(self) -> list[tuple[str, list[str]]]:
        """Non-empty sections, in reading order."""
        return [
            (title, items)
            for title, items in (
                ("Positions", self.positions),
                ("Claims", self.claims),
                ("Challenges", self.challenges),
                ("Concessions", self.concessions),
                ("Unanswered questions", self.unanswered),
            )
            if items
        ]


def argument_map(state: SessionState) -> ArgumentMap:
    graph = state.graph
    config = state.config
    names = {agent.id: agent.name for agent in config.agents}

    def who(node: Node) -> str:
        return names.get(node.speaker_id or "", config.moderator_name)

    def unique(items: list[str]) -> list[str]:
        return list(dict.fromkeys(items))

    result = ArgumentMap()

    # Positions: supporting the topic = for the proposal, challenging it = against.
    for topic in graph.find(node_type=NodeType.TOPIC):
        for_ = unique(
            [who(graph.node(e.source)) for e in graph.incoming(topic.id, EdgeType.SUPPORTS)]
        )
        against = unique(
            [who(graph.node(e.source)) for e in graph.incoming(topic.id, EdgeType.CHALLENGES)]
        )
        if for_:
            result.positions.append(f"For the proposal: {', '.join(for_)}")
        if against:
            result.positions.append(f"Against the proposal: {', '.join(against)}")

    for agent in config.agents:
        for claim in graph.find(speaker_id=agent.id, node_type=NodeType.CLAIM):
            challengers = unique(
                sorted(
                    who(graph.node(e.source)) for e in graph.incoming(claim.id, EdgeType.CHALLENGES)
                )
            )
            supporters = unique(
                sorted(
                    who(graph.node(e.source))
                    for e in graph.incoming(claim.id, EdgeType.SUPPORTS)
                    if graph.node(e.source).speaker_id != claim.speaker_id
                )
            )
            notes = []
            if challengers:
                notes.append(f"challenged by {', '.join(challengers)}")
            if supporters:
                notes.append(f"supported by {', '.join(supporters)}")
            suffix = f"  ({'; '.join(notes)})" if notes else ""
            result.claims.append(f"{agent.name}: {shorten(claim.text)}{suffix}")

    # Challenges: each challenging point once, with whom it was aimed at.
    for node in graph.nodes.values():
        targets = [
            graph.node(e.target)
            for e in graph.outgoing(node.id, EdgeType.CHALLENGES)
            if graph.node(e.target).type is not NodeType.TOPIC
        ]
        if targets:
            aimed_at = ", ".join(unique([who(t) for t in targets]))
            result.challenges.append(f"{who(node)} → {aimed_at}: {shorten(node.text)}")

    result.concessions = unique(
        [
            f"{who(graph.node(e.source))} conceded to {who(graph.node(e.target))}: "
            f"{shorten(graph.node(e.target).text)}"
            for e in graph.edges
            if e.type is EdgeType.CONCEDES_TO
        ]
    )

    for question in graph.find(node_type=NodeType.QUESTION):
        if not graph.incoming(question.id, EdgeType.ANSWERS):
            asked = ", ".join(names.get(a, a) for a in question.addressed_to)
            to = f" (to {asked})" if asked else ""
            result.unanswered.append(f"{who(question)}{to}: {shorten(question.text)}")
    return result
