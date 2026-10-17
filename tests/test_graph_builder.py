"""Tests for the argument graph builder, its link rules, engine wiring and the map summary."""

from __future__ import annotations

import asyncio
import json

from fakes import FakeProvider, default_script, graph_speaker, is_graph_request

from moot.core import (
    ChatMessage,
    EdgeType,
    ModelRef,
    Node,
    NodeType,
    Persona,
    Round,
    RoundPlan,
    RoundType,
    SessionConfig,
    SessionState,
    SpeakerKind,
)
from moot.core.events import EngineNotice, ErrorEvent, GraphUpdated, TurnCompleted
from moot.engine import DiscussionEngine, ProviderPool
from moot.memory import GraphBuilder, GraphExtraction, argument_map

MODEL = ModelRef(provider="fake", model="m")


def make_state() -> SessionState:
    config = SessionConfig(
        topic="Should cities ban cars?",
        agents=[Persona(name="Ava"), Persona(name="Ben"), Persona(name="Cleo")],
        round_plan=RoundPlan(
            rounds=[Round(type=RoundType.OPENING), Round(type=RoundType.CHALLENGE)]
        ),
        default_model=MODEL,
    )
    return SessionState.new(config)


def say(state, name, text, round_index=0, mentions=()):
    agent = state.config.agent_by_name(name)
    message = ChatMessage(
        speaker_id=agent.id,
        speaker_kind=SpeakerKind.AGENT,
        text=text,
        round_index=round_index,
        mentions=list(mentions),
    )
    state.messages.append(message)
    return message


def add_claim(state, name, text):
    agent = state.config.agent_by_name(name)
    return state.graph.add_node(Node(type=NodeType.CLAIM, text=text, speaker_id=agent.id))


def extraction(nodes=(), links=()):
    return GraphExtraction.model_validate({"nodes": list(nodes), "links": list(links)})


# ------------------------------------------------------------------ prompt


def test_prompt_lists_topic_and_points_with_short_refs():
    state = make_state()
    ava_claim = add_claim(state, "Ava", "Bans cut pollution")
    message = say(state, "Ben", "Pollution falls anyway with electric cars.", round_index=1)

    messages, refs = GraphBuilder().build_prompt(state, message)

    user = messages[1].content
    assert "P0 [topic] —: Should cities ban cars?" in user
    assert "P1 [claim] Ava: Bans cut pollution" in user
    assert 'New message from Ben (round 2):\n"""\nPollution falls anyway' in user
    assert refs["P1"] == ava_claim.id
    assert "at most 5 points" in messages[0].content


def test_prompt_keeps_only_recent_points():
    state = make_state()
    for i in range(10):
        add_claim(state, "Ava", f"claim {i}")
    message = say(state, "Ben", "x")
    _, refs = GraphBuilder(context_size=3).build_prompt(state, message)
    texts = [state.graph.node(node_id).text for node_id in refs.values()]
    assert texts == ["Should cities ban cars?", "claim 7", "claim 8", "claim 9"]


# ------------------------------------------------------------------- apply


def test_points_get_speaker_round_and_message_from_the_engine():
    state = make_state()
    message = say(state, "Ava", "Cars pollute. A 2019 study shows it.", round_index=0)
    update = GraphBuilder().apply(
        state,
        message,
        extraction(
            nodes=[
                {"ref": "N1", "type": "claim", "text": "  Cars   pollute city air "},
                {
                    "ref": "N2",
                    "type": "evidence",
                    "text": "A 2019 study",
                    "sources": ["2019 study", " "],
                },
            ],
            links=[{"type": "supports", "source": "N2", "target": "N1"}],
        ),
        refs={},
    )

    claim, evidence = (state.graph.node(i) for i in update.node_ids)
    assert claim.text == "Cars pollute city air"
    assert {claim.speaker_id, evidence.speaker_id} == {message.speaker_id}
    assert claim.round_index == 0 and claim.message_id == message.id
    assert evidence.sources == ["2019 study"]
    assert len(update.edge_ids) == 1
    assert state.graph.edges[0].type is EdgeType.SUPPORTS
    assert update.dropped == []


def test_links_that_break_the_rules_are_dropped():
    state = make_state()
    ava_claim = add_claim(state, "Ava", "Bans cut pollution")
    ben_claim = add_claim(state, "Ben", "Shops suffer")
    refs = {
        "P0": state.graph.find(node_type=NodeType.TOPIC)[0].id,
        "P1": ava_claim.id,
        "P2": ben_claim.id,
    }
    message = say(state, "Ben", "...")

    update = GraphBuilder().apply(
        state,
        message,
        extraction(
            nodes=[
                {"ref": "N1", "type": "claim", "text": "Pollution falls anyway"},
                {"ref": "N2", "type": "concession", "text": "Air quality matters"},
            ],
            links=[
                {"type": "challenges", "source": "N1", "target": "P1"},  # ok
                {"type": "concedes_to", "source": "N2", "target": "P1"},  # ok
                {"type": "answers", "source": "N1", "target": "P1"},  # answers needs a question
                {"type": "challenges", "source": "N1", "target": "P2"},  # own point
                {"type": "supports", "source": "P1", "target": "N1"},  # starts from an old point
                {"type": "supports", "source": "N1", "target": "P9"},  # unknown ref
                {"type": "refines", "source": "N1", "target": "N1"},  # self link
                {"type": "challenges", "source": "N1", "target": "P1"},  # duplicate
            ],
        ),
        refs,
    )

    kept = [(e.type, e.target) for e in state.graph.edges]
    assert kept == [(EdgeType.CHALLENGES, ava_claim.id), (EdgeType.CONCEDES_TO, ava_claim.id)]
    assert len(update.edge_ids) == 2
    assert len(update.dropped) == 5
    assert any("answers cannot go from claim to claim" in d for d in update.dropped)
    assert any("another participant" in d for d in update.dropped)


def test_question_targets_come_from_names_or_mentions():
    state = make_state()
    ben = state.config.agent_by_name("Ben")
    cleo = state.config.agent_by_name("Cleo")

    named = say(state, "Ava", "Cleo, what about buses?")
    update = GraphBuilder().apply(
        state,
        named,
        extraction(
            nodes=[
                {
                    "ref": "N1",
                    "type": "question",
                    "text": "What about buses?",
                    "addressed_to": ["cleo", "Ava", "Nobody"],
                }
            ]
        ),
        refs={},
    )
    assert state.graph.node(update.node_ids[0]).addressed_to == [cleo.id]

    mentioned = say(state, "Ava", "@Ben where is the data?", mentions=[ben.id])
    update = GraphBuilder().apply(
        state,
        mentioned,
        extraction(nodes=[{"ref": "N1", "type": "question", "text": "Where is the data?"}]),
        refs={},
    )
    assert state.graph.node(update.node_ids[0]).addressed_to == [ben.id]


def test_too_many_or_invalid_points_are_limited():
    state = make_state()
    message = say(state, "Ava", "...")
    nodes = [{"ref": f"N{i}", "type": "claim", "text": f"point {i}"} for i in range(1, 9)]
    nodes.append({"ref": "N1", "type": "claim", "text": "duplicate ref"})
    update = GraphBuilder(max_points=3).apply(state, message, extraction(nodes=nodes), refs={})
    assert len(update.node_ids) == 3


def test_extraction_ignores_unknown_fields_but_rejects_unknown_types():
    ok = GraphExtraction.model_validate(
        {"nodes": [{"ref": "N1", "type": "claim", "text": "x", "mood": "angry"}], "extra": 1}
    )
    assert ok.nodes[0].text == "x"
    try:
        GraphExtraction.model_validate({"nodes": [{"ref": "N1", "type": "rumour", "text": "x"}]})
    except ValueError:
        pass
    else:
        raise AssertionError("unknown point type accepted")


# ------------------------------------------------------------------ engine


def run_engine(script, graph_delay=0.0):
    state = make_state()
    provider = FakeProvider(script, graph_delay=graph_delay)
    engine = DiscussionEngine(state, providers=ProviderPool(factory=lambda preset_id: provider))
    asyncio.run(engine.run())
    return state, engine, provider


def test_engine_updates_the_graph_after_every_turn():
    def script(request):
        if is_graph_request(request):
            speaker = graph_speaker(request)
            if speaker == "Ben" and "Ava" in request.messages[-1].content:
                # Ben challenges Ava's most recent claim, listed as the last P-ref
                last_ref = [
                    line.split()[0]
                    for line in request.messages[-1].content.splitlines()
                    if line.startswith("P")
                ][-1]
                return [
                    json.dumps(
                        {
                            "nodes": [{"ref": "N1", "type": "claim", "text": "Ben disagrees"}],
                            "links": [{"type": "challenges", "source": "N1", "target": last_ref}],
                        }
                    )
                ]
            return [
                json.dumps(
                    {
                        "nodes": [{"ref": "N1", "type": "claim", "text": f"{speaker} point"}],
                        "links": [],
                    }
                )
            ]
        return default_script(request)

    state, engine, _ = run_engine(script)

    history = engine.events.history
    turns = [e for e in history if isinstance(e, TurnCompleted)]
    updates = [i for i, e in enumerate(history) if isinstance(e, GraphUpdated)]
    assert len(updates) == len(turns) == 6
    assert max(updates) < len(history) - 1  # all finished before the session ended
    claims = state.graph.find(node_type=NodeType.CLAIM)
    assert len(claims) == 6  # three agents, two rounds
    assert any(e.type is EdgeType.CHALLENGES for e in state.graph.edges)


def test_graph_failure_is_reported_but_the_discussion_goes_on():
    def script(request):
        if is_graph_request(request):
            return ["I refuse to answer in JSON"]
        return default_script(request)

    state, engine, provider = run_engine(script)

    errors = [e for e in engine.events.history if isinstance(e, ErrorEvent)]
    assert len(errors) == 6
    assert all(
        e.message.startswith("Argument graph not updated") and e.agent_id is None for e in errors
    )
    assert len([m for m in state.messages if m.speaker_kind is SpeakerKind.AGENT]) == 6
    assert engine.events.history[-1].reason == "completed"
    assert len([r for r in provider.requests if is_graph_request(r)]) == 12  # one retry each


def test_engine_records_mentions_on_messages():
    def script(request):
        if is_graph_request(request):
            return ['{"nodes": [], "links": []}']
        return ["@Ben, prove it." if "You are Ava" in request.messages[0].content else "Fine."]

    state, _, _ = run_engine(script)
    ben = state.config.agent_by_name("Ben")
    ava_messages = [m for m in state.messages if m.text.startswith("@Ben")]
    assert ava_messages and all(m.mentions == [ben.id] for m in ava_messages)


# ----------------------------------------------------------------- summary


def test_argument_map_summary():
    from moot.core import Edge

    state = make_state()
    graph = state.graph
    topic = graph.find(node_type=NodeType.TOPIC)[0]
    ben = state.config.agent_by_name("Ben")
    cleo = state.config.agent_by_name("Cleo")
    ava_claim = add_claim(state, "Ava", "Bans cut pollution")
    graph.add_edge(Edge(type=EdgeType.SUPPORTS, source=ava_claim.id, target=topic.id))
    ben_claim = graph.add_node(Node(type=NodeType.CLAIM, text="Shops suffer", speaker_id=ben.id))
    graph.add_edge(Edge(type=EdgeType.CHALLENGES, source=ben_claim.id, target=ava_claim.id))
    graph.add_edge(Edge(type=EdgeType.CHALLENGES, source=ben_claim.id, target=topic.id))
    ben_extra = graph.add_node(Node(type=NodeType.CLAIM, text="No data " * 40, speaker_id=ben.id))
    graph.add_edge(Edge(type=EdgeType.CHALLENGES, source=ben_extra.id, target=ava_claim.id))
    support = graph.add_node(Node(type=NodeType.EVIDENCE, text="Paris data", speaker_id=cleo.id))
    graph.add_edge(Edge(type=EdgeType.SUPPORTS, source=support.id, target=ava_claim.id))
    graph.add_node(
        Node(type=NodeType.QUESTION, text="Who pays?", speaker_id=cleo.id, addressed_to=[ben.id])
    )

    summary = argument_map(state)

    assert summary.positions == ["For the proposal: Ava", "Against the proposal: Ben"]
    assert summary.claims[0] == "Ava: Bans cut pollution  (challenged by Ben; supported by Cleo)"
    assert summary.claims[1] == "Ben: Shops suffer"
    assert summary.claims[2].endswith("…") and len(summary.claims[2]) < 160
    assert summary.challenges[0] == "Ben → Ava: Shops suffer"
    assert len(summary.challenges) == 2  # each challenging point once, topic excluded
    assert summary.unanswered == ["Cleo (to Ben): Who pays?"]
    assert [title for title, _ in summary.sections()] == [
        "Positions",
        "Claims",
        "Challenges",
        "Unanswered questions",
    ]


def test_graph_is_built_in_the_background_and_finished_at_the_end():
    state, engine, _ = run_engine(default_script, graph_delay=0.01)

    history = engine.events.history
    types = [e.type for e in history]
    assert types.count("graph_updated") == 6
    assert any(isinstance(e, EngineNotice) and "Finishing" in e.message for e in history)
    # the next turn did not wait for the previous message's graph update
    first_update = types.index("graph_updated")
    assert types.index("turn_started", types.index("turn_completed")) < first_update
    assert len(state.graph.find(node_type=NodeType.CLAIM)) == 6


def test_stopping_drops_pending_graph_work():
    import time

    state = make_state()
    provider = FakeProvider(default_script, graph_delay=5)
    engine = DiscussionEngine(state, providers=ProviderPool(factory=lambda preset_id: provider))

    async def scenario():
        async def watch():
            async for event in engine.events.subscribe():
                if isinstance(event, TurnCompleted):
                    engine.stop()

        watcher = asyncio.create_task(watch())
        await engine.run()
        await watcher

    started = time.perf_counter()
    asyncio.run(scenario())
    assert time.perf_counter() - started < 2
    assert engine.events.history[-1].reason == "stopped"
