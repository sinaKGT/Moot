"""Tests for the shared argument graph."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moot.core import ArgumentGraph, Edge, EdgeType, GraphError, Node, NodeType


@pytest.fixture
def graph():
    g = ArgumentGraph()
    g.add_node(Node(id="topic", type=NodeType.TOPIC, text="Should cities ban cars?"))
    g.add_node(Node(id="c1", type=NodeType.CLAIM, text="Bans cut pollution", speaker_id="ava"))
    g.add_node(Node(id="c2", type=NodeType.CLAIM, text="Bans hurt shops", speaker_id="ben"))
    g.add_node(
        Node(
            id="q1",
            type=NodeType.QUESTION,
            text="What evidence on shops?",
            speaker_id="ava",
            addressed_to=["ben"],
        )
    )
    return g


def test_links_and_queries(graph):
    graph.add_edge(Edge(type=EdgeType.SUPPORTS, source="c1", target="topic", speaker_id="ava"))
    graph.add_edge(Edge(type=EdgeType.CHALLENGES, source="c2", target="c1", speaker_id="ben"))
    graph.add_edge(Edge(type=EdgeType.CHALLENGES, source="q1", target="c2", speaker_id="ava"))

    assert [e.source for e in graph.incoming("c1")] == ["c2"]
    assert [e.target for e in graph.outgoing("c1")] == ["topic"]
    assert graph.incoming("c2", EdgeType.CHALLENGES)[0].source == "q1"
    assert graph.incoming("c2", EdgeType.SUPPORTS) == []
    assert [n.id for n in graph.find(speaker_id="ava")] == ["c1", "q1"]
    assert [n.id for n in graph.find(node_type=NodeType.CLAIM)] == ["c1", "c2"]
    assert graph.node("q1").addressed_to == ["ben"]


def test_same_link_twice_is_kept_once(graph):
    first = graph.add_edge(Edge(type=EdgeType.CHALLENGES, source="c2", target="c1"))
    second = graph.add_edge(Edge(type=EdgeType.CHALLENGES, source="c2", target="c1"))
    assert second is first
    assert len(graph.edges) == 1


def test_invalid_changes_are_rejected(graph):
    with pytest.raises(GraphError, match="already exists"):
        graph.add_node(Node(id="c1", type=NodeType.CLAIM, text="dup"))
    with pytest.raises(GraphError, match="does not exist"):
        graph.add_edge(Edge(type=EdgeType.SUPPORTS, source="c1", target="nope"))
    with pytest.raises(GraphError, match="itself"):
        graph.add_edge(Edge(type=EdgeType.SUPPORTS, source="c1", target="c1"))
    with pytest.raises(GraphError, match="no node"):
        graph.node("nope")


def test_graph_survives_json_and_rejects_broken_files(graph):
    graph.add_edge(Edge(type=EdgeType.CHALLENGES, source="c2", target="c1"))
    restored = ArgumentGraph.model_validate_json(graph.model_dump_json())
    assert restored == graph

    broken = graph.model_dump()
    broken["edges"].append({"id": "e9", "type": "supports", "source": "c1", "target": "ghost"})
    with pytest.raises(ValidationError, match="does not exist"):
        ArgumentGraph.model_validate(broken)


def test_nodes_need_text():
    with pytest.raises(ValidationError):
        Node(type=NodeType.CLAIM, text="")
