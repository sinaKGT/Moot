"""Tests for alliances: the history, agents' alliance actions, detected alignment,
briefings, and the engine and console."""

from __future__ import annotations

import asyncio
import io
import json

import pytest
from fakes import FakeProvider, default_script, moderator_reply, round_of, speaker_of

from moot.console import ConsoleRenderer
from moot.core import (
    Alliance,
    AllianceError,
    AllianceStatus,
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
    SessionRules,
    SessionState,
)
from moot.core.events import AllianceChanged, TokenDelta
from moot.engine import DiscussionEngine, ProviderPool
from moot.engine.alliances import apply_alliance_action, parse_alliance_action
from moot.engine.moderator import REPORT_TASK
from moot.memory import (
    alliance_lines,
    alliance_mismatches,
    alliance_monitor,
    attention_lines,
    build_briefing,
    pair_alignment,
    pending_items,
    relations,
    topic_sides,
)

MODEL = ModelRef(provider="fake", model="fake-model")


def make_state(rounds=None, **config) -> SessionState:
    settings = {
        "topic": "Should cities ban cars?",
        "agents": [Persona(name="Ava"), Persona(name="Ben"), Persona(name="Cleo")],
        "round_plan": RoundPlan(
            rounds=rounds or [Round(type=RoundType.CHALLENGE), Round(type=RoundType.CHALLENGE)]
        ),
        "rules": SessionRules(presets=[]),
        "default_model": MODEL,
    }
    settings.update(config)
    return SessionState.new(SessionConfig(**settings))


def ids(state):
    return [a.id for a in state.config.agents]


def act(state, name, action, round_index=0):
    agent = state.config.agent_by_name(name)
    return apply_alliance_action(
        state, agent, parse_alliance_action(json.dumps(action)), round_index
    )


# ------------------------------------------------------------------- core


def test_history_records_every_step_with_its_round():
    alliance = Alliance.propose("ava", ["ben", "cleo"], goal=" ban cars ", round_index=0)
    alliance.accept("cleo", round_index=1)
    alliance.refuse("ben", round_index=1, reason="I disagree")
    alliance.leave("cleo", round_index=2, reason="changed my mind")

    assert [s.change for s in alliance.history] == [
        "proposed",
        "accepted",
        "refused",
        "left",
        "dissolved",
    ]
    assert alliance.goal == "ban cars"
    assert alliance.history[0].targets == ["ben", "cleo"]
    assert alliance.history[3].reason == "changed my mind"
    assert (alliance.formed_round, alliance.ended_round) == (1, 2)
    assert alliance.everyone == ["ava", "cleo"]
    assert alliance.left == ["cleo"]
    assert alliance.status is AllianceStatus.DISSOLVED


def test_members_can_invite_more_agents():
    alliance = Alliance.propose("ava", ["ben"])
    alliance.accept("ben")
    assert alliance.invite("ben", ["cleo", "ava", "ben"]) == ["cleo"]
    assert alliance.invited == ["cleo"]
    with pytest.raises(AllianceError, match="already"):
        alliance.invite("ava", ["cleo"])
    with pytest.raises(AllianceError, match="not a member"):
        alliance.invite("cleo", ["dan"])


def test_withdrawing_a_proposal_ends_it():
    alliance = Alliance.propose("ava", ["ben", "cleo"])
    alliance.leave("ava")
    assert alliance.status is AllianceStatus.DISSOLVED


# --------------------------------------------------------------- actions


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"action": "propose", "with": "Ben", "goal": "x"}', ("propose", ["Ben"], "")),
        ('```json\n{"action": "Join", "from": "Ava"}\n```', ("accept", [], "Ava")),
        ('{"action": "decline", "to": ["Ava"]}', ("refuse", ["Ava"], "")),
        ('{"action": "break", "reason": "no", "extra": 1}', ("leave", [], "")),
    ],
)
def test_actions_are_read_leniently(text, expected):
    action = parse_alliance_action(text)
    assert (action.action, action.with_, action.from_) == expected


@pytest.mark.parametrize("text", ["no json here", '{"action": "dance"}'])
def test_unreadable_actions_raise(text):
    with pytest.raises(ValueError):
        parse_alliance_action(text)


def test_propose_and_accept():
    state = make_state()
    ava, ben, cleo = ids(state)

    proposed = act(state, "Ava", {"action": "propose", "with": ["Cleo"], "goal": "Ban cars"})
    assert [(e.change, e.agent_id, e.targets) for e in proposed] == [("proposed", ava, [cleo])]
    alliance = state.alliances[0]
    assert (alliance.goal, alliance.status) == ("Ban cars", AllianceStatus.PROPOSED)

    accepted = act(state, "Cleo", {"action": "accept", "from": "Ava"}, round_index=1)
    assert [(e.change, e.agent_id) for e in accepted] == [("accepted", cleo)]
    assert state.active_alliances() == [alliance]
    assert alliance.formed_round == 1
    assert accepted[0].alliance.members == [ava, cleo]  # a snapshot, not the live object
    assert accepted[0].alliance is not alliance
    assert ben not in alliance.members


def test_proposing_back_accepts_the_invitation():
    state = make_state()
    act(state, "Ava", {"action": "propose", "with": ["Cleo"]})
    changes = act(state, "Cleo", {"action": "propose", "with": ["Ava"]})
    assert [e.change for e in changes] == ["accepted"]
    assert len(state.alliances) == 1


def test_proposing_while_allied_invites_into_the_same_alliance():
    state = make_state()
    _, ben, _ = ids(state)
    act(state, "Ava", {"action": "propose", "with": ["Cleo"]})
    act(state, "Cleo", {"action": "accept"})
    changes = act(state, "Cleo", {"action": "propose", "with": ["Ben", "Ava"]})
    assert [(e.change, e.targets) for e in changes] == [("proposed", [ben])]
    assert len(state.alliances) == 1
    with pytest.raises(AllianceError, match="already allied with Ava"):
        act(state, "Cleo", {"action": "propose", "with": ["Ava"]})


def test_refusing_the_only_invitation_ends_the_proposal():
    state = make_state()
    changes = act(state, "Ava", {"action": "propose", "with": ["Ben"]})
    changes = act(state, "Ben", {"action": "refuse", "from": "Ava", "reason": "We disagree"})
    assert [(e.change, e.reason) for e in changes] == [
        ("refused", "We disagree"),
        ("dissolved", ""),
    ]
    assert changes[1].agent_id is None


def test_leaving_ends_a_two_member_alliance():
    state = make_state()
    act(state, "Ava", {"action": "propose", "with": ["Ben"]})
    act(state, "Ben", {"action": "accept"})
    changes = act(state, "Ben", {"action": "leave", "reason": "She ignored my point"}, 2)
    assert [e.change for e in changes] == ["left", "dissolved"]
    assert state.alliances[0].ended_round == 2
    with pytest.raises(AllianceError, match="not in an alliance"):
        act(state, "Ben", {"action": "leave"})


def test_accepting_another_invitation_switches_sides():
    state = make_state()
    act(state, "Ava", {"action": "propose", "with": ["Ben"]})
    act(state, "Ben", {"action": "accept"})
    act(state, "Cleo", {"action": "propose", "with": ["Ben"]})
    changes = act(state, "Ben", {"action": "accept", "from": "Cleo"})
    assert [e.change for e in changes] == ["left", "dissolved", "accepted"]
    first, second = state.alliances
    assert first.status is AllianceStatus.DISSOLVED
    assert second.status is AllianceStatus.ACTIVE


@pytest.mark.parametrize(
    ("name", "action", "message"),
    [
        ("Ben", {"action": "accept"}, "no alliance invitation"),
        ("Ben", {"action": "refuse", "from": "Cleo"}, "no alliance invitation from Cleo"),
        ("Ava", {"action": "propose", "with": ["Zed"]}, "no participant called Zed"),
        ("Ava", {"action": "propose", "with": ["Ava"]}, "at least one other"),
    ],
)
def test_impossible_actions_explain_why(name, action, message):
    state = make_state()
    with pytest.raises(AllianceError, match=message):
        act(state, name, action)
    assert state.alliances == []


# -------------------------------------------------------------- alignment


def add_point(state, node_id, speaker, round_index, node_type=NodeType.CLAIM):
    state.graph.add_node(
        Node(id=node_id, type=node_type, text=node_id, speaker_id=speaker, round_index=round_index)
    )


def link(state, edge_type, source, target):
    state.graph.add_edge(Edge(type=edge_type, source=source, target=target))


@pytest.fixture
def debated():
    """Round 0: Ava and Cleo argue for the topic, Ben against.
    Round 1: Cleo backs Ava, Ben challenges both, Cleo once challenges Ava."""
    state = make_state()
    ava, ben, cleo = ids(state)
    topic = state.graph.find(node_type=NodeType.TOPIC)[0].id
    add_point(state, "a1", ava, 0)
    add_point(state, "b1", ben, 0)
    add_point(state, "c1", cleo, 0)
    link(state, EdgeType.SUPPORTS, "a1", topic)
    link(state, EdgeType.CHALLENGES, "b1", topic)
    link(state, EdgeType.SUPPORTS, "c1", topic)
    add_point(state, "c2", cleo, 1)
    add_point(state, "b2", ben, 1)
    add_point(state, "c3", cleo, 1)
    add_point(state, "a2", ava, 1)
    link(state, EdgeType.SUPPORTS, "c2", "a1")
    link(state, EdgeType.CHALLENGES, "b2", "a1")
    link(state, EdgeType.CHALLENGES, "b2", "c1")
    link(state, EdgeType.CHALLENGES, "c3", "a1")
    link(state, EdgeType.REFINES, "a2", "c2")
    return state


def test_relations_and_pairs_come_from_links_between_agents(debated):
    ava, ben, cleo = ids(debated)
    found = relations(debated)
    assert (found[(cleo, ava)].support, found[(cleo, ava)].challenge) == (1.0, 1.0)
    assert (found[(ava, cleo)].support, found[(ava, cleo)].challenge) == (0.5, 0.0)
    assert found[(ben, ava)].challenge == 1.0

    pairs = {(p.first, p.second): p for p in pair_alignment(debated)}
    assert pairs[(ava, cleo)].score == pytest.approx(0.5 / 2.5)
    assert pairs[(ava, ben)].score == -1.0
    assert pairs[(ben, cleo)].interactions == 1.0


def test_alignment_can_be_seen_up_to_a_round(debated):
    assert relations(debated, up_to_round=0) == {}
    assert all(p.interactions == 0 for p in pair_alignment(debated, up_to_round=0))


def test_topic_sides(debated):
    ava, ben, cleo = ids(debated)
    assert topic_sides(debated) == {ava: "for", ben: "against", cleo: "for"}
    fresh = make_state()
    assert set(topic_sides(fresh).values()) == {None}


def test_allies_who_challenge_each_other_are_flagged(debated):
    ava, _, cleo = ids(debated)
    act(debated, "Ava", {"action": "propose", "with": ["Cleo"]}, round_index=0)
    act(debated, "Cleo", {"action": "accept"}, round_index=1)
    assert alliance_mismatches(debated) == []  # one support, one challenge: not more challenges

    add_point(debated, "c4", cleo, 1)
    link(debated, EdgeType.CHALLENGES, "c4", "a2")
    mismatch = alliance_mismatches(debated)
    assert [(m.challenger, m.ally, m.challenges) for m in mismatch] == [(cleo, ava, 2.0)]
    assert any(
        "Cleo has challenged their ally Ava 2 times" in line for line in alliance_lines(debated)
    )


def test_challenges_before_the_alliance_do_not_count(debated):
    _, ben, cleo = ids(debated)
    act(debated, "Ben", {"action": "propose", "with": ["Cleo"]}, round_index=2)
    act(debated, "Cleo", {"action": "accept"}, round_index=2)
    assert alliance_mismatches(debated) == []  # Ben challenged Cleo in round 1, before allying


def test_monitor_replays_alliances_round_by_round(debated):
    ava, _, cleo = ids(debated)
    act(debated, "Ava", {"action": "propose", "with": ["Cleo"]}, round_index=0)
    act(debated, "Cleo", {"action": "accept"}, round_index=1)

    early = alliance_monitor(debated, up_to_round=0)
    assert [a.status for a in early.alliances] == [AllianceStatus.PROPOSED]
    assert not any(p.allied for p in early.pairs)

    late = alliance_monitor(debated)
    assert [a.status for a in late.alliances] == [AllianceStatus.ACTIVE]
    allied = [p for p in late.pairs if p.allied]
    assert [(p.first, p.second) for p in allied] == [(ava, cleo)]
    assert late.sides[ava] == "for"
    assert survives_json(late)


def survives_json(monitor):
    return type(monitor).model_validate_json(monitor.model_dump_json()) == monitor


# -------------------------------------------------------------- briefings


def test_invitations_need_an_answer_and_count_as_pending():
    state = make_state()
    _, ben, cleo = state.config.agents
    act(state, "Ava", {"action": "propose", "with": ["Cleo"], "goal": "Ban cars"})

    lines = attention_lines(state, cleo)
    assert lines == [
        "- Ava invited you to an alliance: Ban cars. Accept or refuse it in this answer."
    ]
    assert pending_items(state, cleo) == 1
    assert pending_items(state, ben) == 0
    ava = state.config.agents[0]
    assert "You invited Cleo to an alliance; no answer yet" in build_briefing(state, ava)


def test_everyone_sees_who_is_allied_and_broken_alliances():
    state = make_state()
    ava, ben, cleo = state.config.agents
    act(state, "Ava", {"action": "propose", "with": ["Cleo"], "goal": "Ban cars"}, 0)
    act(state, "Cleo", {"action": "accept"}, 1)

    assert "- You are allied with Cleo (since round 2): Ban cars" in build_briefing(state, ava)
    assert "- Ava and Cleo are allied (since round 2): Ban cars" in build_briefing(state, ben)
    assert "Alliances (everyone can see these):" in build_briefing(state, ben)

    act(state, "Cleo", {"action": "leave", "reason": "Ava ignored the costs"}, 2)
    briefing = build_briefing(state, ben)
    assert (
        "The alliance of Ava and Cleo ended in round 3 when Cleo left: Ava ignored the costs"
        in (briefing)
    )
    assert "are allied" not in briefing


def test_no_alliance_lines_when_alliances_are_off():
    state = make_state(allow_alliances=False)
    act(state, "Ava", {"action": "propose", "with": ["Cleo"]})
    cleo = state.config.agents[2]
    assert attention_lines(state, cleo) == []
    assert alliance_lines(state) == []


# ----------------------------------------------------------------- engine

PROPOSE = '<alliance>{"action": "propose", "with": ["Cleo"], "goal": "Ban cars"}</alliance>'
ACCEPT = '<alliance>{"action": "accept", "from": "Ava"}</alliance>'


def alliance_script(request):
    reply = moderator_reply(request)
    if reply is not None:
        return reply
    speaker, round_number = speaker_of(request), round_of(request)
    if (speaker, round_number) == ("Ava", 1):
        return ["Cleo, let's team up. ", PROPOSE]
    if (speaker, round_number) == ("Cleo", 1):
        return ["I accept, Ava.", ACCEPT]
    if (speaker, round_number) == ("Ben", 1):
        return ["I join nobody.", '<alliance>{"action": "accept"}</alliance>']
    return default_script(request)


def make_engine(state, script=alliance_script, **kwargs):
    provider = FakeProvider(script)
    kwargs.setdefault("moderator_enabled", False)
    kwargs.setdefault("build_graph", False)
    pool = ProviderPool(factory=lambda preset_id: provider)
    return DiscussionEngine(state, providers=pool, **kwargs), provider


def prompts_for(provider, name):
    return [r.messages[-1].content for r in provider.requests if speaker_of(r) == name]


def test_agents_propose_and_accept_during_the_discussion():
    state = make_state()
    engine, provider = make_engine(state)

    asyncio.run(engine.run())

    alliance = state.alliances[0]
    assert alliance.status is AllianceStatus.ACTIVE
    assert [state.config.speaker_label(a) for a in alliance.members] == ["Ava", "Cleo"]
    changes = [e for e in engine.events.history if isinstance(e, AllianceChanged)]
    assert [e.change for e in changes] == ["proposed", "accepted"]

    shown = "".join(e.text for e in engine.events.history if isinstance(e, TokenDelta))
    assert "<alliance>" not in shown and '"action"' not in shown
    assert all("<alliance>" not in m.text for m in state.messages)

    assert "Alliances (optional and public)" in prompts_for(provider, "Ava")[0]
    cleo_first = prompts_for(provider, "Cleo")[0]
    assert "Ava invited you to an alliance: Ban cars. Accept or refuse it" in cleo_first
    ben_second = prompts_for(provider, "Ben")[1]
    assert "- Ava and Cleo are allied (since round 1): Ban cars" in ben_second


def test_an_impossible_action_is_explained_before_the_next_turn():
    state = make_state()
    engine, provider = make_engine(state)
    asyncio.run(engine.run())
    ben_prompts = prompts_for(provider, "Ben")
    expected = "Your alliance action could not be done: you have no alliance invitation."
    assert expected not in ben_prompts[0]
    assert expected in ben_prompts[1]


def test_alliance_lines_are_ignored_in_blind_rounds():
    state = make_state(rounds=[Round(type=RoundType.OPENING), Round(type=RoundType.CLOSING)])
    engine, provider = make_engine(state)
    asyncio.run(engine.run())
    assert state.alliances == []
    assert "Alliances (optional and public)" not in prompts_for(provider, "Ava")[0]
    assert "Alliances (optional and public)" in prompts_for(provider, "Ava")[1]


def test_alliances_can_be_switched_off():
    state = make_state(allow_alliances=False)
    engine, provider = make_engine(state)
    asyncio.run(engine.run())
    assert state.alliances == []
    assert not any("Alliances (optional" in p for p in prompts_for(provider, "Ava"))


def test_the_moderator_hears_about_alliances():
    state = make_state()
    engine, provider = make_engine(state, moderator_enabled=True)
    asyncio.run(engine.run())
    moderator_prompts = [
        r.messages[-1].content
        for r in provider.requests
        if "the moderator of a discussion" in r.messages[0].content
    ]
    summary = next(p for p in moderator_prompts if "has just ended." in p)
    assert "Alliances:\n- Ava and Cleo are allied (since round 1): Ban cars" in summary
    report = next(p for p in moderator_prompts if REPORT_TASK in p)
    assert "- who teamed up, and whether the alliances held" in report


def test_console_reports_alliance_changes_and_a_summary():
    state = make_state()
    engine, _ = make_engine(state)
    out = io.StringIO()
    renderer = ConsoleRenderer(state.config, out=out, color=False, state=state)

    async def scenario():
        follower = asyncio.create_task(renderer.follow(engine.events.subscribe()))
        await engine.run()
        await follower

    asyncio.run(scenario())
    renderer.render_alliances(state)
    text = out.getvalue()
    assert "· Ava proposes an alliance to Cleo: Ban cars" in text
    assert "· Cleo joins the alliance with Ava" in text
    assert "── Alliances ──" in text
    assert "  • Ava and Cleo (since round 1): Ban cars" in text


def test_console_describes_leaving_and_ending():
    state = make_state()
    renderer = ConsoleRenderer(state.config, out=io.StringIO(), color=False, state=state)
    act(state, "Ava", {"action": "propose", "with": ["Ben"]})
    act(state, "Ben", {"action": "accept"})
    events = act(state, "Ben", {"action": "leave", "reason": "too extreme"})
    for event in events:
        renderer.handle(event)
    text = renderer.out.getvalue()
    assert "· Ben leaves the alliance with Ava: too extreme" in text
    assert "· The alliance of Ava and Ben has ended" in text
