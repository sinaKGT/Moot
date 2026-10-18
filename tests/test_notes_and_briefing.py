"""Tests for private notes, the stream tag filter, briefings and the recent-message window."""

from __future__ import annotations

import asyncio
import json

import pytest
from fakes import FakeProvider, is_graph_request

from moot.core import (
    ChatMessage,
    Edge,
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
from moot.core.events import NotebookUpdated, TokenDelta
from moot.engine import DiscussionEngine, ProviderPool
from moot.engine.prompts import RECENT_MESSAGES, turn_prompt
from moot.engine.text import TagFilter
from moot.memory import apply_notes, argument_map, build_briefing, parse_notes, split_trailing_notes
from moot.providers import render

MODEL = ModelRef(provider="fake", model="m")


def make_state(rounds=None) -> SessionState:
    config = SessionConfig(
        topic="Should cities ban cars?",
        agents=[Persona(name="Ava"), Persona(name="Ben"), Persona(name="Cleo")],
        round_plan=RoundPlan(
            rounds=rounds or [Round(type=RoundType.OPENING), Round(type=RoundType.CHALLENGE)]
        ),
        default_model=MODEL,
    )
    return SessionState.new(config)


# -------------------------------------------------------------- tag filter


def run_filter(chunks):
    tags = TagFilter()
    visible = "".join(tags.feed(chunk) for chunk in chunks) + tags.flush()
    return visible, tags.captured


@pytest.mark.parametrize(
    "chunks",
    [
        ['Yes. Cars pollute.<notes>{"verdict": "yes"}</notes>'],
        ["Yes. Cars pollute.<no", 'tes>{"verdict"', ': "yes"}</no', "tes>"],
        ["Yes. Cars ", "pollute.", "<", "notes", ">", '{"verdict": "yes"}', "<", "/notes>"],
    ],
)
def test_notes_are_captured_and_hidden(chunks):
    visible, captured = run_filter(chunks)
    assert visible == "Yes. Cars pollute."
    assert captured == {"notes": '{"verdict": "yes"}'}


def test_thinking_is_hidden_and_text_streams():
    tags = TagFilter()
    assert tags.feed("<think>let me ") == ""
    assert tags.feed("consider</think>No, ") == "No, "
    assert tags.feed("because <b>shops</b> suffer.") == "because <b>shops</b> suffer."
    assert tags.flush() == ""
    assert tags.captured == {}


def test_unclosed_sections_stay_hidden():
    visible, captured = run_filter(['Answer.<notes>{"verdict": "no"'])
    assert visible == "Answer."
    assert captured["notes"] == '{"verdict": "no"'


def test_text_that_only_looks_like_a_tag_is_kept():
    visible, _ = run_filter(["a < b and <n", "ote that"])
    assert visible == "a < b and <note that"


# ------------------------------------------------------------------- notes


def test_parse_notes_normalises_values():
    update = parse_notes(
        'Notes: {"verdict": "I don\'t know", "confidence": "80%", "key_arguments": "one", '
        '"allies": ["Ben", "", "Ben"], "unknown": 1}'
    )
    assert update.verdict == "unsure"
    assert update.confidence == 0.8
    assert update.key_arguments == ["one"]
    assert parse_notes('{"verdict": "YES", "confidence": 3}').confidence == 0.03
    assert parse_notes('{"verdict": "maybe?"}').verdict is None
    with pytest.raises(ValueError):
        parse_notes("no json here")


def test_apply_notes_maps_names_and_records_the_round():
    state = make_state()
    ava, ben, cleo = state.config.agents
    update = parse_notes(
        json.dumps(
            {
                "verdict": "yes",
                "confidence": 0.7,
                "stance": "Cars should go.",
                "allies": ["cleo", "Ava", "Nobody"],
                "opponents": ["Ben"],
                "judgements": {"Ben": "weak on data", "Ava": "self", "Zed": "?"},
                "strategy": "Press Ben on shops.",
            }
        )
    )
    notebook = apply_notes(state, ava, update, round_index=1)

    assert notebook.verdict == "yes" and notebook.confidence == 0.7
    assert notebook.allies == [cleo.id]
    assert notebook.opponents == [ben.id]
    assert notebook.judgements == {ben.id: "weak on data"}
    assert notebook.history[-1].round_index == 1
    assert notebook.history[-1].verdict == "yes"

    apply_notes(state, ava, parse_notes('{"confidence": 0.4}'), round_index=2)
    assert notebook.stance == "Cars should go."  # missing fields keep their value
    assert [s.confidence for s in notebook.history] == [0.7, 0.4]


def test_trailing_notes_without_tags_are_moved_out():
    visible, notes = split_trailing_notes('No, shops suffer.\n```json\n{"verdict": "no"}\n```')
    assert visible == "No, shops suffer."
    assert json.loads(notes) == {"verdict": "no"}
    assert split_trailing_notes("Just text {not json}") == ("Just text {not json}", "")
    assert split_trailing_notes('Data: {"a": 1}') == ('Data: {"a": 1}', "")


# ---------------------------------------------------------------- briefing


def test_briefing_lists_notes_challenges_questions_and_open_points():
    state = make_state()
    graph = state.graph
    ava, ben, cleo = state.config.agents
    mine = graph.add_node(Node(type=NodeType.CLAIM, text="Bans cut pollution", speaker_id=ava.id))
    attack = graph.add_node(Node(type=NodeType.CLAIM, text="Shops suffer", speaker_id=ben.id))
    graph.add_edge(Edge(type=EdgeType.CHALLENGES, source=attack.id, target=mine.id))
    graph.add_node(
        Node(type=NodeType.QUESTION, text="Who pays?", speaker_id=cleo.id, addressed_to=[ava.id])
    )
    graph.add_node(Node(type=NodeType.CLAIM, text="Buses are cheap", speaker_id=cleo.id))
    apply_notes(
        state, ava, parse_notes('{"verdict": "yes", "confidence": 0.6, "stance": "Ban them."}'), 0
    )

    briefing = build_briefing(state, ava)

    assert "Your answer so far: YES (60% sure): Ban them." in briefing
    assert 'Ben challenged your point "Bans cut pollution": Shops suffer' in briefing
    assert "Cleo asked you: Who pays?" in briefing
    assert "Point you haven't answered, from Cleo: Buses are cheap" in briefing

    # once Ava challenges Ben's point, it no longer needs her attention
    reply = graph.add_node(Node(type=NodeType.CLAIM, text="Footfall rises", speaker_id=ava.id))
    graph.add_edge(Edge(type=EdgeType.CHALLENGES, source=reply.id, target=attack.id))
    assert "Ben challenged your point" not in build_briefing(state, ava)


def test_briefing_respects_blind_rounds():
    state = make_state()
    ava, ben, _ = state.config.agents
    state.graph.add_node(
        Node(type=NodeType.CLAIM, text="Secret opening", speaker_id=ben.id, round_index=0)
    )
    assert "Secret opening" not in build_briefing(state, ava, hidden_round=0)
    assert "Secret opening" in build_briefing(state, ava, hidden_round=None)


def test_long_discussions_use_a_recent_window_plus_earlier_points():
    state = make_state([Round(type=RoundType.FREE, turns_per_agent=5)])
    ava, ben, _ = state.config.agents
    for i in range(RECENT_MESSAGES + 3):
        speaker = ben if i % 2 else ava
        message = ChatMessage(
            speaker_id=speaker.id,
            speaker_kind=SpeakerKind.AGENT,
            text=f"message {i}",
            round_index=0,
        )
        state.messages.append(message)
        if i == 0:
            state.graph.add_node(
                Node(
                    type=NodeType.CLAIM,
                    text="First big claim",
                    speaker_id=ava.id,
                    message_id=message.id,
                )
            )

    prompt = render(turn_prompt(state, ava, 0, state.config.round_plan.rounds[0], 2))

    assert "Earlier in the discussion:\n- Ava: First big claim" in prompt
    assert "Recent messages:" in prompt
    assert "message 0" not in prompt  # replaced by its graph point
    assert f"message {RECENT_MESSAGES + 2}" in prompt


# ------------------------------------------------------------------ engine


def test_engine_hides_notes_and_updates_the_notebook():
    def script(request):
        if is_graph_request(request):
            return ['{"nodes": [], "links": []}']
        if "You are Ava" in request.messages[0].content:
            return [
                "Yes.\n\n",
                "Cars pollute.\n\n",
                '<notes>{"verdict": "yes", "confidence": 0.9}</notes>',
            ]
        return ["No. Shops suffer."]  # Ben forgets his notes

    state = make_state([Round(type=RoundType.OPENING)])
    provider = FakeProvider(script)
    engine = DiscussionEngine(
        state,
        providers=ProviderPool(factory=lambda preset_id: provider),
        moderator_enabled=False,
    )
    asyncio.run(engine.run())

    ava, ben, _ = state.config.agents
    texts = [m.text for m in state.messages if m.speaker_kind is SpeakerKind.AGENT]
    assert texts[0] == "Yes.\n\nCars pollute."
    ava_message_id = state.messages[0].id
    deltas = "".join(
        e.text
        for e in engine.events.history
        if isinstance(e, TokenDelta) and e.message_id == ava_message_id
    )
    assert deltas == "Yes.\n\nCars pollute."  # inner blank line kept, trailing ones dropped
    assert state.notebooks[ava.id].verdict == "yes"
    assert state.notebooks[ben.id].verdict is None
    updated = [e.agent_id for e in engine.events.history if isinstance(e, NotebookUpdated)]
    assert updated == [ava.id]

    summary = argument_map(state)
    assert summary.final_positions == ["Ava: YES (90% sure)"]


def test_trailing_space_holder():
    from moot.engine.text import TrailingSpaceHolder

    holder = TrailingSpaceHolder()
    assert holder.feed("One.  ") == "One."
    assert holder.feed("\n\n") == ""
    assert holder.feed("Two.") == "  \n\nTwo."
    assert holder.feed(" ") == ""
    assert holder.flush() == ""
