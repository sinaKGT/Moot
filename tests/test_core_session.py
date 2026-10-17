"""Tests for session config and state, notebooks and events."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moot.core import (
    MODERATOR_ID,
    AIModerator,
    Alliance,
    ChatMessage,
    HumanModerator,
    ModelRef,
    NodeType,
    Notebook,
    Persona,
    Poll,
    SessionConfig,
    SessionState,
    SessionStatus,
    SpeakerKind,
    TokenUsage,
    parse_event,
    winner_poll_spec,
)
from moot.core.events import AllianceChanged, PollOpened, TokenDelta, TurnCompleted

LOCAL = ModelRef(provider="lmstudio", model="google/gemma-4-12b-qat")
CLOUD = ModelRef(provider="openrouter", model="nvidia/nemotron-3-super-120b-a12b:free")


def make_config(**overrides) -> SessionConfig:
    settings = {
        "topic": "Should cities ban private cars from their centres?",
        "agents": [Persona(name="Ava"), Persona(name="Ben"), Persona(name="Cleo", model=CLOUD)],
        "default_model": LOCAL,
    }
    settings.update(overrides)
    return SessionConfig(**settings)


def test_config_defaults():
    config = make_config()
    assert isinstance(config.moderator, AIModerator)
    assert config.moderator_name == "Moderator"
    assert len(config.round_plan) == 5  # classic debate
    assert [p.value for p in config.rules.presets] == ["answer_first", "one_point"]


def test_model_resolution():
    config = make_config()
    ava, _, cleo = config.agents
    assert config.model_for(ava) == LOCAL
    assert config.model_for(cleo) == CLOUD
    assert config.moderator_model() == LOCAL
    assert config.graph_model() == LOCAL

    explicit = make_config(graph_builder_model=CLOUD, moderator=AIModerator(model=CLOUD))
    assert explicit.graph_model() == CLOUD
    assert explicit.moderator_model() == CLOUD


def test_every_agent_needs_a_model():
    with pytest.raises(ValidationError, match="'Ava' has no model"):
        make_config(default_model=None)


def test_human_moderator_needs_a_graph_model():
    human = HumanModerator(display_name="Sina", role="Judge")
    agents = [Persona(name="Ava", model=LOCAL), Persona(name="Ben", model=LOCAL)]
    with pytest.raises(ValidationError, match="graph builder"):
        make_config(moderator=human, agents=agents, default_model=None)

    config = make_config(
        moderator=human, agents=agents, default_model=None, graph_builder_model=CLOUD
    )
    assert config.moderator_name == "Sina"
    assert config.moderator_model() is None
    assert config.graph_model() == CLOUD


def test_names_must_be_unique_including_the_moderator():
    with pytest.raises(ValidationError, match="unique"):
        make_config(agents=[Persona(name="Ava"), Persona(name="ava")])
    with pytest.raises(ValidationError, match="both called"):
        make_config(moderator=AIModerator(name="Ava"))


def test_config_needs_two_agents_and_a_topic():
    with pytest.raises(ValidationError):
        make_config(agents=[Persona(name="Ava")])
    with pytest.raises(ValidationError):
        make_config(topic="   ")


def test_find_agents_by_name_or_id():
    config = make_config()
    ben = config.agents[1]
    assert config.agent_by_name("@ben") is ben
    assert config.agent_by_name("Nobody") is None
    assert config.agent(ben.id) is ben
    with pytest.raises(ValueError):
        config.agent("agt_missing")


def test_new_session_has_topic_node_and_notebooks():
    state = SessionState.new(make_config())
    assert state.status is SessionStatus.SETUP
    topic_nodes = state.graph.find(node_type=NodeType.TOPIC)
    assert [n.text for n in topic_nodes] == [state.config.topic]
    assert set(state.notebooks) == {a.id for a in state.config.agents}


def test_state_rejects_notebooks_for_unknown_agents():
    config = make_config()
    with pytest.raises(ValidationError, match="unknown agents"):
        SessionState(config=config, notebooks={"agt_x": Notebook(agent_id="agt_x")})


def test_notebook_snapshots_one_per_round():
    notebook = Notebook(agent_id="ava", stance="Ban cars", confidence=0.8)
    notebook.snapshot(0)
    notebook.confidence = 0.6
    notebook.snapshot(0)
    notebook.stance = "Ban cars at weekends"
    notebook.snapshot(1)
    assert [(s.round_index, s.confidence) for s in notebook.history] == [(0, 0.6), (1, 0.6)]
    assert notebook.history[-1].stance == "Ban cars at weekends"


def test_full_state_survives_json():
    state = SessionState.new(make_config())
    ava, ben, cleo = state.config.agents
    state.messages.append(
        ChatMessage(
            speaker_id=ava.id,
            speaker_kind=SpeakerKind.AGENT,
            text="@Ben where is your evidence?",
            round_index=1,
            mentions=[ben.id],
            usage=TokenUsage(prompt_tokens=120, completion_tokens=12),
        )
    )
    alliance = Alliance.propose(ava.id, [cleo.id])
    alliance.accept(cleo.id)
    state.alliances.append(alliance)
    poll = Poll.open(winner_poll_spec(), state.config.agents, opened_by=MODERATOR_ID)
    poll.cast_vote(ava.id, f"opt_{cleo.id}")
    state.polls.append(poll)
    state.notebooks[ava.id].stance = "Ban cars"

    restored = SessionState.from_json(state.to_json())

    assert restored == state
    assert restored.active_alliances() == [alliance]
    assert restored.open_polls() == [poll]


def test_events_round_trip_through_json():
    message = ChatMessage(speaker_id="ava", speaker_kind=SpeakerKind.AGENT, text="Hello")
    alliance = Alliance.propose("ava", ["ben"])
    poll = Poll.open(
        winner_poll_spec(), [Persona(name="Ava"), Persona(name="Ben")], opened_by=MODERATOR_ID
    )
    events = [
        TokenDelta(seq=1, message_id="msg_1", text="Hel"),
        TurnCompleted(seq=2, message=message),
        AllianceChanged(seq=3, change="proposed", agent_id="ava", alliance=alliance),
        PollOpened(seq=4, poll=poll),
    ]
    for event in events:
        restored = parse_event(event.model_dump_json())
        assert type(restored) is type(event)
        assert restored == event
    assert isinstance(
        parse_event({"type": "session_paused"}), type(parse_event('{"type": "session_paused"}'))
    )
    with pytest.raises(ValidationError):
        parse_event({"type": "nonsense"})
