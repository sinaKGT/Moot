"""Tests for polls: voting, vote rounds, polls opened by the user or the moderator,
results in briefings, the console chart and terminal commands."""

from __future__ import annotations

import asyncio
import io
import json

import pytest
from fakes import FakeProvider, default_script, is_vote_request, moderator_reply, speaker_of

from moot.console import ConsoleRenderer
from moot.core import (
    MODERATOR_ID,
    USER_ID,
    HumanModerator,
    ModelRef,
    Persona,
    PollKind,
    PollOption,
    PollSpec,
    PollStatus,
    Round,
    RoundPlan,
    RoundType,
    SessionConfig,
    SessionRules,
    SessionState,
    SessionStatus,
    SpeakerKind,
)
from moot.core.events import ErrorEvent, PollClosed, PollOpened, TurnCompleted, VoteCast
from moot.demo import listen_to_terminal, terminal_command
from moot.engine import DiscussionEngine, ProviderPool
from moot.engine.moderator import POLL_OFFER
from moot.engine.voting import match_choice, parse_poll_spec
from moot.providers import ProviderConnectionError

MODEL = ModelRef(provider="fake", model="fake-model")
QUESTION = "Who made the strongest case?"


def challenge_rounds(count=1):
    return [Round(type=RoundType.CHALLENGE) for _ in range(count)]


def make_state(rounds=None, names=("Ava", "Ben", "Cleo"), **config) -> SessionState:
    settings = {
        "topic": "Should cities ban cars?",
        "agents": [Persona(name=name) for name in names],
        "round_plan": RoundPlan(rounds=rounds or challenge_rounds()),
        "rules": SessionRules(presets=[]),
        "default_model": MODEL,
    }
    settings.update(config)
    return SessionState.new(SessionConfig(**settings))


def make_engine(state, script=None, **kwargs):
    provider = FakeProvider(script)
    kwargs.setdefault("moderator_enabled", False)
    kwargs.setdefault("build_graph", False)
    pool = ProviderPool(factory=lambda preset_id: provider)
    return DiscussionEngine(state, providers=pool, **kwargs), provider


def voting(choices: dict[str, list[str] | str]):
    """A script where each agent votes as given (a list = successive answers)."""
    asked: dict[str, int] = {}

    def script(request):
        if not is_vote_request(request):
            return default_script(request)
        name = speaker_of(request)
        answers = choices.get(name, "A")
        if not isinstance(answers, list):
            answers = [answers]
        attempt = asked.get(name, 0)
        asked[name] = attempt + 1
        answer = answers[min(attempt, len(answers) - 1)]
        if isinstance(answer, Exception):
            return answer
        return [json.dumps({"choice": answer})]

    return script


def vote_prompts(provider, name=None):
    return [
        r.messages[-1].content
        for r in provider.requests
        if is_vote_request(r) and (name is None or speaker_of(r) == name)
    ]


def votes_by_name(state, poll):
    names = {a.id: a.name for a in state.config.agents}
    labels = {o.id: o.label for o in poll.options}
    return {names[voter]: labels[option] for voter, option in poll.votes.items()}


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- reading votes

OPTIONS = [PollOption(id="opt_0", label="Ben"), PollOption(id="opt_1", label="Cleo")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A", "Ben"),
        ("b", "Cleo"),
        ("B)", "Cleo"),
        ("B) Cleo", "Cleo"),
        ("(A)", "Ben"),
        ("2", "Cleo"),
        ("cleo", "Cleo"),
        ("@Ben", "Ben"),
        ("opt_1", "Cleo"),
        ("Z", None),
        ("3", None),
        ("", None),
        ("Nobody", None),
    ],
)
def test_votes_are_read_as_letter_number_or_label(text, expected):
    option = match_choice(text, OPTIONS)
    assert (option.label if option else None) == expected


def test_an_exact_label_wins_over_a_letter():
    options = [PollOption(id="o0", label="Ban now"), PollOption(id="o1", label="A slow phase-out")]
    assert match_choice("A slow phase-out", options).id == "o1"
    assert match_choice("A", options).id == "o0"


def test_moderator_polls_are_read_leniently():
    spec = parse_poll_spec('<x>{"question": "Ban cars?", "options": ["Yes", "No"]}')
    assert (spec.question, spec.options, spec.kind) == ("Ban cars?", ["Yes", "No"], PollKind.CHOICE)
    assert parse_poll_spec('{"winner": true}').kind is PollKind.WINNER
    assert parse_poll_spec("{}").question == QUESTION
    for bad in ('{"question": "Q", "options": "Yes"}', '{"question": "Q", "options": ["One"]}'):
        with pytest.raises(ValueError):
            parse_poll_spec(bad)


# ---------------------------------------------------------------- polls in the engine


def test_the_user_can_open_a_poll_and_every_agent_votes():
    state = make_state()
    engine, provider = make_engine(state, voting({"Ava": "B", "Ben": "Cleo", "Cleo": "1"}))
    engine.open_poll()

    run(engine.run())

    poll = state.polls[0]
    assert (poll.question, poll.kind, poll.opened_by) == (QUESTION, PollKind.WINNER, USER_ID)
    assert poll.status is PollStatus.CLOSED
    assert votes_by_name(state, poll) == {"Ava": "Cleo", "Ben": "Cleo", "Cleo": "Ava"}
    assert poll.round_index == 0

    kinds = [
        type(e) for e in engine.events.history if isinstance(e, PollOpened | VoteCast | PollClosed)
    ]
    assert kinds == [PollOpened, VoteCast, VoteCast, VoteCast, PollClosed]
    closed = next(e for e in engine.events.history if isinstance(e, PollClosed))
    assert [(r.label, r.votes) for r in closed.results] == [("Ava", 1), ("Ben", 0), ("Cleo", 2)]

    ava_ballot = vote_prompts(provider, "Ava")[0]
    assert f'User has opened a poll: "{QUESTION}"' in ava_ballot
    assert "A) Ben\nB) Cleo\n(You cannot vote for yourself.)" in ava_ballot
    assert "Ava\n" not in ava_ballot.split("Choose exactly one option:")[1]
    assert "Poll results" not in ava_ballot  # nobody sees votes in the same poll


def test_a_choice_poll_and_its_results_in_later_briefings():
    state = make_state(rounds=challenge_rounds(2))
    engine, provider = make_engine(state, voting({"Ava": "Yes", "Ben": "B", "Cleo": "A"}))
    engine.open_poll(PollSpec(question="Ban cars?", options=["Yes", "No"]))

    run(engine.run())

    poll = state.polls[0]
    assert votes_by_name(state, poll) == {"Ava": "Yes", "Ben": "No", "Cleo": "Yes"}
    assert "(You cannot vote for yourself.)" not in vote_prompts(provider, "Ava")[0]
    ben_turns = [
        r.messages[-1].content
        for r in provider.requests
        if speaker_of(r) == "Ben" and not is_vote_request(r)
    ]
    assert "Poll results (everyone can see these):" in ben_turns[0]
    assert '"Ban cars?" (round 1): Yes 2 votes (Ava and Cleo), No 1 vote (Ben).' in ben_turns[0]
    assert "You voted No." in ben_turns[0]


def test_a_vote_round_runs_the_poll_in_the_plan():
    spec = PollSpec(question="Who should change their mind?", kind=PollKind.WINNER)
    state = make_state(rounds=[*challenge_rounds(), Round(type=RoundType.VOTE, poll=spec)])
    engine, _ = make_engine(state)
    run(engine.run())
    poll = state.polls[0]
    assert (poll.question, poll.opened_by, poll.round_index) == (spec.question, MODERATOR_ID, 1)
    assert len(poll.votes) == 3


def test_a_wrong_option_gets_one_more_try():
    state = make_state()
    engine, provider = make_engine(state, voting({"Ben": ["Z", "B"]}))
    engine.open_poll()
    run(engine.run())
    assert votes_by_name(state, state.polls[0])["Ben"] == "Cleo"
    retry = [r for r in provider.requests if is_vote_request(r) and speaker_of(r) == "Ben"][-1]
    assert "That is not one of the options" in retry.messages[-1].content


def test_agents_who_cannot_vote_are_left_out():
    state = make_state()
    script = voting({"Ben": "Z", "Cleo": ProviderConnectionError("Fake", "down")})
    engine, _ = make_engine(state, script)
    engine.open_poll()
    run(engine.run())

    poll = state.polls[0]
    assert set(votes_by_name(state, poll)) == {"Ava"}
    notices = [m.text for m in state.messages if m.speaker_kind is SpeakerKind.SYSTEM]
    assert "Ben did not vote." in notices and "Cleo did not vote." in notices
    errors = [e for e in engine.events.history if isinstance(e, ErrorEvent)]
    assert [e.agent_id for e in errors] == [state.config.agents[2].id]
    assert engine.events.history[-1].reason == "completed"  # voting failures don't end it


def test_repeating_a_poll_compares_with_the_last_one():
    state = make_state(rounds=challenge_rounds(2))
    engine, provider = make_engine(state)
    engine.open_poll()

    async def scenario():
        async def control():
            async for event in engine.events.subscribe():
                if isinstance(event, TurnCompleted) and event.message.round_index == 1:
                    engine.open_poll()
                    return

        task = asyncio.create_task(control())
        await engine.run()
        await task

    run(scenario())
    first, second = state.polls
    assert second.previous_id == first.id
    later_turn = [r.messages[-1].content for r in provider.requests if not is_vote_request(r)][-1]
    assert "The same poll earlier: Ava 2 votes, Ben 1 vote, Cleo 0 votes." in later_turn


def test_polls_run_while_paused():
    state = make_state()
    engine, _ = make_engine(state)
    seen = []

    async def scenario():
        async def control():
            async for event in engine.events.subscribe():
                if isinstance(event, TurnCompleted) and not seen:
                    seen.append(event)
                    engine.pause()
                    engine.open_poll()
                elif isinstance(event, PollClosed):
                    seen.append(state.status)
                    engine.resume()

        task = asyncio.create_task(control())
        await engine.run()
        await task

    run(scenario())
    assert seen[1] is SessionStatus.PAUSED
    assert state.status is SessionStatus.FINISHED


def test_open_poll_after_the_end_is_refused():
    state = make_state()
    engine, _ = make_engine(state)
    run(engine.run())
    with pytest.raises(RuntimeError, match="finished"):
        engine.open_poll()


def test_a_human_moderator_opens_polls_as_the_moderator():
    human = HumanModerator(display_name="Sina", role="Judge")
    state = make_state(moderator=human, graph_builder_model=MODEL)
    engine, provider = make_engine(state)
    engine.open_poll()
    run(engine.run())
    assert state.polls[0].opened_by == MODERATOR_ID
    assert f'Sina has opened a poll: "{QUESTION}"' in vote_prompts(provider)[0]


# ---------------------------------------------------------------- the AI moderator

POLL_LINE = '<poll>{"question": "Ban cars?", "options": ["Yes", "No"]}</poll>'


def moderator_polls(request):
    if "the moderator of a discussion" in request.messages[0].content:
        if "has just ended." in request.messages[-1].content:
            return ["Summary of the round.", POLL_LINE]
    return moderator_reply(request) or default_script(request)


def moderator_prompts(provider, marker):
    return [
        r.messages[-1].content
        for r in provider.requests
        if "the moderator of a discussion" in r.messages[0].content
        and marker in r.messages[-1].content
    ]


def test_the_moderator_may_open_one_poll_after_a_summary():
    state = make_state(rounds=challenge_rounds(3))
    engine, provider = make_engine(state, moderator_polls, moderator_enabled=True)
    run(engine.run())

    assert [(p.question, p.opened_by, p.round_index) for p in state.polls] == [
        ("Ban cars?", MODERATOR_ID, 0)
    ]
    summaries = moderator_prompts(provider, "has just ended.")
    assert POLL_OFFER in summaries[0]
    assert POLL_OFFER not in summaries[1]  # one moderator poll per session by default
    assert "Poll results so far:" in summaries[1]
    assert all("<poll>" not in m.text for m in state.messages)
    report = moderator_prompts(provider, "Write the final report")[0]
    assert "Vote: what the polls showed" in report
    assert '"Ban cars?" (round 1): Yes 3 votes' in report


def test_no_poll_offer_right_before_a_vote_round():
    state = make_state(rounds=[*challenge_rounds(), Round(type=RoundType.VOTE)])
    engine, provider = make_engine(state, moderator_polls, moderator_enabled=True)
    run(engine.run())
    assert POLL_OFFER not in moderator_prompts(provider, "has just ended.")[0]
    assert [p.opened_by for p in state.polls] == [MODERATOR_ID]  # just the vote round


# ---------------------------------------------------------------- console and terminal


def test_console_draws_the_results_like_a_poll():
    state = make_state()
    engine, _ = make_engine(state)
    engine.open_poll()
    out = io.StringIO()
    renderer = ConsoleRenderer(state.config, out=out, color=False, state=state)

    async def scenario():
        follower = asyncio.create_task(renderer.follow(engine.events.subscribe()))
        await engine.run()
        await follower

    run(scenario())
    text = out.getvalue()
    assert f"── Poll: {QUESTION} ──" in text
    assert "Opened by You. Options: Ava, Ben, Cleo" in text
    assert "  ✓ Ava voted" in text
    assert "  Ava   █████████████░░░░░░░  2 votes   67%  ← Ben, Cleo" in text
    assert "  Cleo  ░░░░░░░░░░░░░░░░░░░░  0 votes    0%" in text


def test_terminal_commands():
    state = make_state(rounds=challenge_rounds(2))
    engine, _ = make_engine(state)
    shown = []

    assert terminal_command(engine, "/poll") == "The poll will start after the current turn."
    terminal_command(engine, "/poll Ban cars? | Now | Later | Never")
    terminal_command(engine, "/poll Is it fair?")
    terminal_command(engine, "@Ben why?")
    assert "Unknown command /dance" in terminal_command(engine, "/dance")
    assert terminal_command(engine, "   ") is None

    async def scenario():
        thread = listen_to_terminal(
            engine, asyncio.get_running_loop(), io.StringIO("/poll Q | only one\n"), shown.append
        )
        thread.join(timeout=5)
        await asyncio.sleep(0)
        await engine.run()

    run(scenario())
    assert [(p.question, [o.label for o in p.options]) for p in state.polls] == [
        (QUESTION, ["Ava", "Ben", "Cleo"]),
        ("Ban cars?", ["Now", "Later", "Never"]),
        ("Is it fair?", ["Yes", "No", "Not sure"]),
    ]
    assert any(m.text == "@Ben why?" for m in state.messages)
    assert shown and shown[0].startswith("That didn't work:")


def test_pause_and_resume_commands():
    state = make_state()
    engine, _ = make_engine(state)
    states = []

    async def scenario():
        async def control():
            async for event in engine.events.subscribe():
                if isinstance(event, TurnCompleted) and not states:
                    terminal_command(engine, "/pause")
                    states.append(state.status)
                    terminal_command(engine, "/resume")
                    states.append(state.status)

        task = asyncio.create_task(control())
        await engine.run()
        await task

    run(scenario())
    assert states == [SessionStatus.PAUSED, SessionStatus.RUNNING]
