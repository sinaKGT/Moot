"""Tests for the AI moderator: opening, speaker choice, summaries, rule reminders, report."""

from __future__ import annotations

import asyncio
import io
import json

from fakes import FakeProvider, default_script, moderator_reply, run_moderated, speaker_of

from moot.core import (
    MODERATOR_ID,
    ChatMessage,
    Edge,
    EdgeType,
    HumanModerator,
    ModelRef,
    Node,
    NodeType,
    Persona,
    Round,
    RoundPlan,
    RoundType,
    RulePreset,
    SessionConfig,
    SessionRules,
    SessionState,
    SpeakerKind,
    SpeakingOrder,
)
from moot.core.events import ErrorEvent, ModeratorDecision, RuleViolation, TurnCompleted
from moot.demo import demo_config, run_in_console
from moot.engine import DiscussionEngine, ProviderPool
from moot.engine.moderator import SELECTION_TASK, ModeratorBrain, check_topic
from moot.engine.rule_checks import check_rules, is_yes_no_topic, word_count
from moot.providers import ProviderConnectionError

MODEL = ModelRef(provider="fake", model="m")


def make_state(rounds=None, moderator=None, rules=None, agents=("Ava", "Ben")) -> SessionState:
    settings = {
        "topic": "Should cities ban cars?",
        "agents": [Persona(name=name) for name in agents],
        "round_plan": RoundPlan(
            rounds=rounds or [Round(type=RoundType.OPENING), Round(type=RoundType.CHALLENGE)]
        ),
        "rules": rules or SessionRules(presets=[]),
        "default_model": MODEL,
    }
    if moderator is not None:
        settings["moderator"] = moderator
    return SessionState.new(SessionConfig(**settings))


def run(state, script=default_script, **kwargs):
    provider = FakeProvider(script)
    engine = DiscussionEngine(
        state,
        providers=ProviderPool(factory=lambda preset_id: provider),
        build_graph=False,
        **kwargs,
    )
    human = isinstance(state.config.moderator, HumanModerator)
    asyncio.run(run_moderated(engine) if human else engine.run())
    return engine, provider


def moderator_texts(state):
    return [m.text for m in state.messages if m.speaker_kind is SpeakerKind.MODERATOR]


# -------------------------------------------------------- opening & summary


def test_moderator_opens_summarises_between_rounds_and_reports():
    state = make_state()
    engine, provider = run(state)

    first = state.messages[0]
    assert first.speaker_kind is SpeakerKind.MODERATOR and first.round_index is None
    assert first.text.startswith("Welcome.")
    assert "<plan>" not in first.text
    subquestions = [n.text for n in state.graph.find(node_type=NodeType.SUBQUESTION)]
    assert subquestions == ["Is it healthy?", "Is it practical?"]

    texts = moderator_texts(state)
    assert texts.count("Summary of the round.") == 1  # between the two rounds, not after the last
    assert texts[-1] == "Answer: undecided."
    assert state.report == "Answer: undecided."
    assert engine.events.history[-1].reason == "completed"

    # agents hear the moderator
    ava_prompts = [r for r in provider.requests if speaker_of(r) == "Ava"]
    assert "Moderator: Welcome." in ava_prompts[0].messages[-1].content


def test_no_report_when_stopped():
    state = make_state()
    provider = FakeProvider()
    engine = DiscussionEngine(
        state, providers=ProviderPool(factory=lambda preset_id: provider), build_graph=False
    )

    async def scenario():
        async def watch():
            async for event in engine.events.subscribe():
                if (
                    isinstance(event, TurnCompleted)
                    and event.message.speaker_kind is SpeakerKind.AGENT
                ):
                    engine.stop()

        watcher = asyncio.create_task(watch())
        await engine.run()
        await watcher

    asyncio.run(scenario())
    assert state.report is None
    assert engine.events.history[-1].reason == "stopped"


def test_moderator_failures_never_stop_the_discussion():
    def script(request):
        if moderator_reply(request) is not None:
            return ProviderConnectionError("Fake", "moderator model down")
        return default_script(request)

    state = make_state()
    engine, _ = run(state, script)

    errors = [e.message for e in engine.events.history if isinstance(e, ErrorEvent)]
    assert errors and all("moderator" in e for e in errors)
    assert len([m for m in state.messages if m.speaker_kind is SpeakerKind.AGENT]) == 4
    assert moderator_texts(state) == []
    assert engine.events.history[-1].reason == "completed"


def test_human_moderator_means_no_ai_moderator_turns():
    state = make_state(
        moderator=HumanModerator(display_name="Sina", role="Judge"),
        rules=SessionRules(presets=[RulePreset.ANSWER_FIRST]),
    )
    state.config.graph_builder_model = MODEL
    engine, provider = run(state)

    assert moderator_texts(state) == []
    assert all(
        "the moderator of a discussion" not in r.messages[0].content for r in provider.requests
    )
    violations = [e for e in engine.events.history if isinstance(e, RuleViolation)]
    assert len(violations) == 4  # "Ava round 1" doesn't start with Yes / No


# ------------------------------------------------------------ speaker order


def _order(state):
    return [
        state.config.agent(m.speaker_id).name
        for m in state.messages
        if m.speaker_kind is SpeakerKind.AGENT
    ]


def test_moderator_chooses_who_speaks():
    def script(request):
        user = request.messages[-1].content
        if SELECTION_TASK in user:  # always pick the last person still waiting
            waiting = [
                line[2:].split(":")[0]
                for line in user.splitlines()
                if line.startswith("- ") and "turns used" in line
            ]
            return [json.dumps({"next": waiting[-1], "reason": "has not spoken"})]
        return default_script(request)

    rounds = [Round(type=RoundType.FREE, speaking_order=SpeakingOrder.MODERATOR)]
    state = make_state(rounds=rounds, agents=("Ava", "Ben", "Cleo"))
    engine, _ = run(state, script)

    assert _order(state) == ["Cleo", "Ben", "Ava"]
    decisions = [e for e in engine.events.history if isinstance(e, ModeratorDecision)]
    assert len(decisions) == 2  # the last one left needs no choice
    assert all(d.reason == "has not spoken" for d in decisions)


def test_nobody_speaks_twice_in_a_row_while_others_wait():
    def script(request):
        if SELECTION_TASK in request.messages[-1].content:
            return ['{"next": "Ava", "reason": "Ava again"}']
        return default_script(request)

    rounds = [Round(type=RoundType.FREE, speaking_order=SpeakingOrder.MODERATOR, turns_per_agent=2)]
    state = make_state(rounds=rounds)
    run(state, script)

    assert _order(state) == ["Ava", "Ben", "Ava", "Ben"]


def test_bad_moderator_choice_falls_back_to_a_fair_pick():
    def script(request):
        if SELECTION_TASK in request.messages[-1].content:
            return ['{"next": "Nobody", "reason": "?"}']
        return default_script(request)

    rounds = [Round(type=RoundType.FREE, speaking_order=SpeakingOrder.MODERATOR)]
    state = make_state(rounds=rounds)
    engine, _ = run(state, script)

    agents = [m for m in state.messages if m.speaker_kind is SpeakerKind.AGENT]
    assert len(agents) == 2 and agents[0].speaker_id != agents[1].speaker_id
    decisions = [e for e in engine.events.history if isinstance(e, ModeratorDecision)]
    assert decisions[0].reason.endswith("spoken least in this round")


def test_free_order_gives_the_floor_to_whoever_was_challenged():
    state = make_state(rounds=[Round(type=RoundType.FREE, speaking_order=SpeakingOrder.FREE)])
    ava, ben = state.config.agents
    target = state.graph.add_node(Node(type=NodeType.CLAIM, text="Bens point", speaker_id=ben.id))
    attack = state.graph.add_node(Node(type=NodeType.CLAIM, text="Ava objects", speaker_id=ava.id))
    state.graph.add_edge(Edge(type=EdgeType.CHALLENGES, source=attack.id, target=target.id))

    engine, _ = run(state, moderator_enabled=False)

    first = next(m for m in state.messages if m.speaker_kind is SpeakerKind.AGENT)
    assert first.speaker_id == ben.id
    decision = next(e for e in engine.events.history if isinstance(e, ModeratorDecision))
    assert decision.reason == "Ben has 1 challenge or question to answer"


# -------------------------------------------------------------------- rules


def test_rule_checks():
    state = make_state(rules=SessionRules(presets=[RulePreset.ANSWER_FIRST], max_words_per_turn=10))
    config, current = state.config, state.config.round_plan.rounds[0]

    assert check_rules(config, current, "Yes. Cars pollute the air in cities.") == []
    assert check_rules(config, current, "No, " + "word " * 10) == []  # 11 words: within tolerance
    assert check_rules(config, current, "No, " + "word " * 20) == ["Use at most 10 words."]
    assert check_rules(config, current, "Cars pollute.") == [
        "Start with a direct answer (Yes, No or I don't know, when the question allows it), "
        "then explain briefly if needed."
    ]
    assert check_rules(config, current, "I don't know; it depends on the city.") == []

    assert is_yes_no_topic("Should cities ban cars?")
    assert is_yes_no_topic("does six pack matter")
    assert not is_yes_no_topic("What is the best way to cut traffic?")
    assert word_count("It's a well-known fact.") == 4


def test_rule_breaks_get_a_moderator_reminder_and_reach_the_next_prompt():
    state = make_state(rules=SessionRules(presets=[RulePreset.ANSWER_FIRST]))
    engine, provider = run(state)

    reminders = [t for t in moderator_texts(state) if "a reminder" in t]
    assert reminders[0].startswith("Ava, a reminder: Start with a direct answer")
    ava_prompts = [r.messages[-1].content for r in provider.requests if speaker_of(r) == "Ava"]
    assert "About your last answer:" not in ava_prompts[0]
    assert "About your last answer:" in ava_prompts[1]
    assert any(isinstance(e, RuleViolation) for e in engine.events.history)


# ---------------------------------------------------------------- helpers


def test_parse_plan_falls_back_to_question_lines():
    brain = ModeratorBrain()
    assert brain.parse_plan('{"subquestions": ["A?", " ", "B?"]}') == ["A?", "B?"]
    text = "Welcome.\n1. Is it healthy?\n- Is it cheap?\nNot a list item?\n2) Who decides?"
    assert brain.parse_plan("", text) == ["Is it healthy?", "Is it cheap?", "Who decides?"]


def test_check_topic():
    unclear = FakeProvider(lambda request: ['{"clear": false, "question": "Did you mean does?"}'])
    check = asyncio.run(check_topic("dose six pack good?", "", unclear, MODEL))
    assert not check.clear and check.question == "Did you mean does?"
    assert asyncio.run(check_topic("Is X good?", "", FakeProvider(), MODEL)).clear


def test_report_material_includes_positions_over_time():
    state = make_state()
    ava = state.config.agents[0]
    notebook = state.notebooks[ava.id]
    notebook.verdict, notebook.confidence = "yes", 0.8
    notebook.snapshot(0)
    notebook.verdict, notebook.confidence = "unsure", 0.5
    notebook.snapshot(1)
    state.messages.append(
        ChatMessage(speaker_id=MODERATOR_ID, speaker_kind=SpeakerKind.MODERATOR, text="Last words")
    )
    prompt = ModeratorBrain().report_messages(state)[1].content
    assert "Ava: round 1 YES (80%), round 2 UNSURE (50%)" in prompt
    assert "Moderator: Last words" in prompt


def test_console_shows_moderator_lines_and_choices():
    state = SessionState.new(demo_config("Should we test?", MODEL))
    out = io.StringIO()
    pool = ProviderPool(factory=lambda preset_id: FakeProvider())
    asyncio.run(run_in_console(state, providers=pool, out=out, color=False))

    text = out.getvalue()
    assert "Moderator: Welcome." in text
    assert "Moderator: Summary of the round." in text
    assert "· Moderator gives the floor to" in text
    assert "Moderator: Answer: undecided." in text
