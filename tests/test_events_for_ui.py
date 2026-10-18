"""Events carry what the app needs to draw the round table: verdicts and who-did-what links."""

from __future__ import annotations

import asyncio
import json

from fakes import FakeProvider, default_script, graph_speaker, is_graph_request, speaker_of

from moot.core import (
    ArgumentGraph,
    ModelRef,
    Persona,
    Round,
    RoundPlan,
    RoundType,
    SessionConfig,
    SessionState,
)
from moot.core.events import EVENT_ADAPTER, GraphUpdated, NotebookUpdated, SessionStarted
from moot.engine import DiscussionEngine, ProviderPool

MODEL = ModelRef(provider="fake", model="fake-model")
NOTES = '<notes>{"verdict": "yes", "confidence": 0.7, "stance": "Ban them"}</notes>'


def script(request):
    if is_graph_request(request):
        speaker = graph_speaker(request)
        if speaker == "Ben":  # Ben's point challenges the first existing point after the topic
            extraction = {
                "nodes": [{"ref": "N1", "type": "claim", "text": "Traffic moves elsewhere"}],
                "links": [{"type": "challenges", "source": "N1", "target": "P1"}],
            }
        else:
            extraction = {"nodes": [{"ref": "N1", "type": "claim", "text": f"{speaker} point"}]}
        return [json.dumps(extraction)]
    reply = default_script(request)
    return [*reply, NOTES] if speaker_of(request) in ("Ava", "Ben") else reply


def test_notebook_and_graph_events_carry_what_the_round_table_shows():
    config = SessionConfig(
        topic="Should cities ban cars?",
        agents=[Persona(name="Ava"), Persona(name="Ben")],
        round_plan=RoundPlan(rounds=[Round(type=RoundType.CLOSING)]),
        default_model=MODEL,
    )
    state = SessionState.new(config)
    provider = FakeProvider(script)
    engine = DiscussionEngine(
        state,
        providers=ProviderPool(factory=lambda preset_id: provider),
        moderator_enabled=False,
    )
    asyncio.run(engine.run())
    ava, ben = config.agents

    notebooks = [e for e in engine.events.history if isinstance(e, NotebookUpdated)]
    assert [(e.agent_id, e.verdict, e.confidence) for e in notebooks] == [
        (ava.id, "yes", 0.7),
        (ben.id, "yes", 0.7),
    ]

    links = [link for e in engine.events.history if isinstance(e, GraphUpdated) for link in e.links]
    assert len(links) == 1
    link = links[0]
    assert (link.type, link.source_speaker, link.target_speaker) == ("challenges", ben.id, ava.id)
    ben_message = next(m for m in state.messages if m.speaker_id == ben.id)
    assert link.message_id == ben_message.id


def test_the_event_stream_rebuilds_the_argument_graph():
    """The argument map is drawn from events alone: the graph at the start, then each update."""
    config = SessionConfig(
        topic="Should cities ban cars?",
        agents=[Persona(name="Ava"), Persona(name="Ben")],
        round_plan=RoundPlan(rounds=[Round(type=RoundType.OPENING), Round(type=RoundType.CLOSING)]),
        default_model=MODEL,
    )
    state = SessionState.new(config)
    provider = FakeProvider(script)
    engine = DiscussionEngine(
        state,
        providers=ProviderPool(factory=lambda preset_id: provider),
        moderator_enabled=False,
    )
    asyncio.run(engine.run())

    # as a viewer gets them: serialized and parsed back
    events = [
        EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(e)) for e in engine.events.history
    ]
    rebuilt = ArgumentGraph()
    for event in events:
        if isinstance(event, SessionStarted):
            rebuilt = event.graph.model_copy(deep=True)
        elif isinstance(event, GraphUpdated):
            for node in event.nodes:
                rebuilt.add_node(node)
            for edge in event.edges:
                rebuilt.add_edge(edge)
    assert rebuilt == state.graph
    assert len(state.graph.nodes) > 1 and state.graph.edges  # the topic plus real points and links
