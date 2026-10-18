"""The graph builder: after each turn, map the message's points into the argument graph.

One model call per turn returns the new points and their links. The engine, not the
model, decides who said each point and when, and every link is checked against
`LINK_RULES` before it is added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from moot.core import (
    ChatMessage,
    Edge,
    EdgeType,
    ModelRef,
    Node,
    NodeType,
    SessionState,
)
from moot.providers import ChatRequest, Message, Provider, complete_structured, trimmable

GRAPH_BUILDER_INTRO = "You map the arguments in a moderated discussion."

MAX_POINT_WORDS = 30
MAX_POINT_CHARS = 300
MAX_QUESTION_CHARS = 400  # how much of the question a reply answers is shown

# Which links may connect which kinds of points: (allowed sources, allowed targets).
LINK_RULES: dict[EdgeType, tuple[frozenset[NodeType], frozenset[NodeType]]] = {
    EdgeType.SUPPORTS: (
        frozenset({NodeType.CLAIM, NodeType.EVIDENCE}),
        frozenset({NodeType.CLAIM, NodeType.SUBQUESTION, NodeType.TOPIC}),
    ),
    EdgeType.CHALLENGES: (
        frozenset({NodeType.CLAIM, NodeType.EVIDENCE, NodeType.QUESTION}),
        frozenset({NodeType.CLAIM, NodeType.EVIDENCE, NodeType.TOPIC}),
    ),
    EdgeType.ANSWERS: (
        frozenset({NodeType.CLAIM, NodeType.EVIDENCE, NodeType.CONCESSION}),
        frozenset({NodeType.QUESTION, NodeType.SUBQUESTION}),
    ),
    EdgeType.REFINES: (frozenset({NodeType.CLAIM}), frozenset({NodeType.CLAIM})),
    EdgeType.CONCEDES_TO: (
        frozenset({NodeType.CONCESSION}),
        frozenset({NodeType.CLAIM, NodeType.EVIDENCE}),
    ),
}

# Links that only make sense between different participants.
OTHER_SPEAKER_ONLY = frozenset({EdgeType.CHALLENGES, EdgeType.CONCEDES_TO})


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ExtractedPoint(_Lenient):
    ref: str
    type: Literal["claim", "evidence", "question", "concession"]
    text: str
    addressed_to: list[str] = []
    sources: list[str] = []


class ExtractedLink(_Lenient):
    type: Literal["supports", "challenges", "answers", "refines", "concedes_to"]
    source: str
    target: str


class GraphExtraction(_Lenient):
    nodes: list[ExtractedPoint] = []
    links: list[ExtractedLink] = []


@dataclass
class GraphUpdate:
    node_ids: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # why each rejected piece was dropped


SYSTEM_PROMPT = "\n".join(
    [
        f"{GRAPH_BUILDER_INTRO} For each new message you identify the points it makes and",
        "how they connect to points already made.",
        "",
        "Point types you can create:",
        "- claim: an assertion or position",
        "- evidence: a fact, example, study or source offered to back a claim",
        "- question: a question the speaker asks",
        "- concession: the speaker accepts or agrees with a point made by someone else",
        "",
        "Link types, always from a point in the new message to another point:",
        "- supports: backs up a claim, a sub-question, or the topic (arguing for the proposal)",
        "- challenges: disputes another participant's claim or evidence, or the topic",
        "  (arguing against the proposal)",
        "- answers: responds to a question",
        "- refines: narrows or improves an earlier claim",
        "- concedes_to: a concession accepting another participant's claim or evidence",
        "",
        "Rules:",
        "- Extract only what the new message actually says. Summarise each point in one",
        f"  short sentence of at most {MAX_POINT_WORDS} words.",
        "- Extract at most {max_points} points; skip greetings and filler.",
        "- Give new points the refs N1, N2, ... Refer to existing points by the refs listed",
        "  (P0, P1, ...). Never invent other refs.",
        "- For a question, put the names of the participants it is addressed to in",
        '  "addressed_to". For evidence, put any named sources in "sources".',
        "- Reply with only a JSON object in this shape:",
        '{"nodes": [{"ref": "N1", "type": "claim", "text": "...", "addressed_to": [],'
        ' "sources": []}], "links": [{"type": "challenges", "source": "N1", "target": "P3"}]}',
    ]
)


class GraphBuilder:
    def __init__(self, *, context_size: int = 40, max_points: int = 5) -> None:
        self.context_size = context_size
        self.max_points = max_points

    async def update(
        self, state: SessionState, message: ChatMessage, provider: Provider, model: ModelRef
    ) -> GraphUpdate:
        """Ask the model for this message's points and add them to the graph."""
        messages, refs = self.build_prompt(state, message)
        extraction = await complete_structured(
            provider,
            ChatRequest(model=model.model, messages=messages, temperature=0),
            GraphExtraction,
        )
        return self.apply(state, message, extraction, refs)

    def build_prompt(
        self, state: SessionState, message: ChatMessage
    ) -> tuple[list[Message], dict[str, str]]:
        """The prompt, plus a map from short refs (P0, P1, ...) to node ids."""
        graph = state.graph
        topic_nodes = graph.find(node_type=NodeType.TOPIC) + graph.find(
            node_type=NodeType.SUBQUESTION
        )
        others = [
            n for n in graph.nodes.values() if n.type not in (NodeType.TOPIC, NodeType.SUBQUESTION)
        ]
        context = topic_nodes + others[-self.context_size :]

        refs: dict[str, str] = {}
        lines = []
        for index, node in enumerate(context):
            ref = f"P{index}"
            refs[ref] = node.id
            speaker = self._speaker(state, node.speaker_id) if node.speaker_id else "—"
            lines.append(f"{ref} [{node.type.value}] {speaker}: {node.text}")
        if len(lines) > len(topic_nodes):  # a small context window drops the oldest points
            lines[len(topic_nodes) :] = [trimmable(lines[len(topic_nodes) :])]

        speaker = self._speaker(state, message.speaker_id)
        round_label = (
            f" (round {message.round_index + 1})" if message.round_index is not None else ""
        )
        user = (
            "Existing points:\n"
            + ("\n".join(lines) if lines else "None yet.")
            + f'\n\nNew message from {speaker}{round_label}:\n"""\n{message.text}\n"""'
        )
        question = next((m for m in state.messages if m.id == message.reply_to), None)
        if question is not None:
            asker = self._speaker(state, question.speaker_id)
            user += (
                f"\n\nThis message answers {asker}, who asked:\n"
                f'"""\n{question.text[:MAX_QUESTION_CHARS]}\n"""'
            )
        system = SYSTEM_PROMPT.replace("{max_points}", str(self.max_points))
        return [Message("system", system), Message("user", user)], refs

    def apply(
        self,
        state: SessionState,
        message: ChatMessage,
        extraction: GraphExtraction,
        refs: dict[str, str],
    ) -> GraphUpdate:
        """Add the extracted points and the links that pass the rules."""
        update = GraphUpdate()
        graph = state.graph
        config = state.config
        new_refs: dict[str, str] = {}

        for point in extraction.nodes[: self.max_points]:
            text = " ".join(point.text.split())[:MAX_POINT_CHARS]
            if not text or point.ref in new_refs or point.ref in refs:
                update.dropped.append(f"point {point.ref}: empty or duplicate ref")
                continue
            node_type = NodeType(point.type)
            addressed = [
                agent.id
                for name in point.addressed_to
                if (agent := config.agent_by_name(name)) and agent.id != message.speaker_id
            ]
            if node_type is NodeType.QUESTION and not addressed:
                addressed = list(message.mentions)
            node = graph.add_node(
                Node(
                    type=node_type,
                    text=text,
                    speaker_id=message.speaker_id,
                    round_index=message.round_index,
                    message_id=message.id,
                    addressed_to=list(dict.fromkeys(addressed)),
                    sources=[s.strip() for s in point.sources if s.strip()],
                )
            )
            new_refs[point.ref] = node.id
            update.node_ids.append(node.id)

        for link in extraction.links:
            source_id = new_refs.get(link.source)
            target_id = new_refs.get(link.target) or refs.get(link.target)
            edge_type = EdgeType(link.type)
            reason = self._reject_reason(state, edge_type, source_id, target_id)
            if reason:
                update.dropped.append(f"{link.type} {link.source}->{link.target}: {reason}")
                continue
            before = len(graph.edges)
            edge = graph.add_edge(
                Edge(
                    type=edge_type,
                    source=source_id,
                    target=target_id,
                    speaker_id=message.speaker_id,
                )
            )
            if len(graph.edges) > before:
                update.edge_ids.append(edge.id)
        return update

    @staticmethod
    def _reject_reason(
        state: SessionState, edge_type: EdgeType, source_id: str | None, target_id: str | None
    ) -> str | None:
        if source_id is None:
            return "links must start from a point in the new message"
        if target_id is None:
            return "unknown target"
        if source_id == target_id:
            return "a point cannot link to itself"
        source, target = state.graph.node(source_id), state.graph.node(target_id)
        allowed_sources, allowed_targets = LINK_RULES[edge_type]
        if source.type not in allowed_sources or target.type not in allowed_targets:
            return f"{edge_type.value} cannot go from {source.type.value} to {target.type.value}"
        if edge_type in OTHER_SPEAKER_ONLY and source.speaker_id == target.speaker_id:
            return f"{edge_type.value} must target another participant's point"
        return None

    @staticmethod
    def _speaker(state: SessionState, speaker_id: str | None) -> str:
        return state.config.speaker_label(speaker_id)


__all__ = [
    "GRAPH_BUILDER_INTRO",
    "LINK_RULES",
    "ExtractedLink",
    "ExtractedPoint",
    "GraphBuilder",
    "GraphExtraction",
    "GraphUpdate",
]
