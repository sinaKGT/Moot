"""The shared argument graph: the public record of who said what and how arguments connect."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from moot.core.base import MootModel, new_id


class GraphError(ValueError):
    """An invalid graph change, such as linking to a node that doesn't exist."""


class NodeType(StrEnum):
    TOPIC = "topic"
    SUBQUESTION = "subquestion"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    QUESTION = "question"
    CONCESSION = "concession"


class EdgeType(StrEnum):
    SUPPORTS = "supports"
    CHALLENGES = "challenges"
    ANSWERS = "answers"
    REFINES = "refines"
    CONCEDES_TO = "concedes_to"


class Node(MootModel):
    id: str = Field(default_factory=lambda: new_id("nod"))
    type: NodeType
    text: str = Field(min_length=1, description="short summary of the point")
    speaker_id: str | None = Field(default=None, description="None for the topic itself")
    round_index: int | None = None
    message_id: str | None = Field(default=None, description="the chat message it came from")
    addressed_to: list[str] = Field(
        default_factory=list, description="agents a question is aimed at"
    )
    sources: list[str] = Field(default_factory=list, description="citations for evidence")


class Edge(MootModel):
    id: str = Field(default_factory=lambda: new_id("edg"))
    type: EdgeType
    source: str
    target: str
    speaker_id: str | None = None


class ArgumentGraph(MootModel):
    nodes: dict[str, Node] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_integrity(self) -> ArgumentGraph:
        for key, node in self.nodes.items():
            if key != node.id:
                raise ValueError(f"node stored under {key} has id {node.id}")
        for edge in self.edges:
            self._check_edge(edge)
        return self

    def add_node(self, node: Node) -> Node:
        if node.id in self.nodes:
            raise GraphError(f"node {node.id} already exists")
        self.nodes[node.id] = node
        return node

    def add_edge(self, edge: Edge) -> Edge:
        """Add a link. Adding the same link (type, source, target) twice returns the first one."""
        self._check_edge(edge)
        for existing in self.edges:
            if (existing.type, existing.source, existing.target) == (
                edge.type,
                edge.source,
                edge.target,
            ):
                return existing
        self.edges.append(edge)
        return edge

    def node(self, node_id: str) -> Node:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise GraphError(f"no node {node_id}") from None

    def outgoing(self, node_id: str, edge_type: EdgeType | None = None) -> list[Edge]:
        return [
            e
            for e in self.edges
            if e.source == node_id and (edge_type is None or e.type is edge_type)
        ]

    def incoming(self, node_id: str, edge_type: EdgeType | None = None) -> list[Edge]:
        return [
            e
            for e in self.edges
            if e.target == node_id and (edge_type is None or e.type is edge_type)
        ]

    def find(
        self, *, speaker_id: str | None = None, node_type: NodeType | None = None
    ) -> list[Node]:
        """Nodes matching the filters, in the order they were added."""
        return [
            n
            for n in self.nodes.values()
            if (speaker_id is None or n.speaker_id == speaker_id)
            and (node_type is None or n.type is node_type)
        ]

    def _check_edge(self, edge: Edge) -> None:
        if edge.source not in self.nodes:
            raise GraphError(f"link source {edge.source} does not exist")
        if edge.target not in self.nodes:
            raise GraphError(f"link target {edge.target} does not exist")
        if edge.source == edge.target:
            raise GraphError("a node cannot link to itself")
