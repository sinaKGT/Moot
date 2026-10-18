"""Saving discussions: they survive a restart, reopen exactly as they were, and export."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from fakes import FakeProvider
from fastapi.testclient import TestClient

from moot.core import (
    ModelRef,
    Persona,
    Round,
    RoundPlan,
    RoundType,
    SessionConfig,
    SessionState,
    SessionStatus,
)
from moot.core.events import SessionStarted, TokenDelta, TurnStarted
from moot.engine import EventStream
from moot.server import SessionManager, SessionNotFound, create_app
from moot.storage import (
    SessionStore,
    StoreError,
    export_filename,
    export_json,
    export_markdown,
)

MODEL = ModelRef(provider="fake", model="fake-model")


def config(**extra) -> SessionConfig:
    return SessionConfig(
        topic="Should cities ban cars?",
        agents=[Persona(name="Ava", role="Advocate"), Persona(name="Ben", role="Critic")],
        round_plan=RoundPlan(rounds=[Round(type=RoundType.OPENING), Round(type=RoundType.VOTE)]),
        default_model=MODEL,
        **extra,
    )


def manager_on(path) -> SessionManager:
    return SessionManager(provider_factory=lambda preset: FakeProvider(), store=SessionStore(path))


async def run_to_end(manager: SessionManager, session_config: SessionConfig) -> SessionState:
    record = manager.create(session_config)
    manager.start(record.id)
    assert record.task is not None
    await record.task
    return record.state


def dumped(events) -> list[str]:
    return [event.model_dump_json() for event in events]


# ------------------------------------------------------------------ surviving a restart


def test_a_finished_discussion_reopens_exactly_as_it_was(tmp_path):
    path = tmp_path / "moot.db"

    async def first_run():
        manager = manager_on(path)
        state = await run_to_end(manager, config())
        history = manager.get(state.id).engine.events.history
        await manager.shutdown()
        return state, history

    state, history = asyncio.run(first_run())
    assert any(e.type == "token_delta" for e in history)

    manager = manager_on(path)  # the server started again
    [(listing, record)] = manager.listings()
    assert record is None  # listed without loading it
    assert (listing.id, listing.status, listing.end_reason) == (
        state.id,
        SessionStatus.FINISHED,
        "completed",
    )
    assert listing.agents == ["Ava", "Ben"]

    reopened = manager.get(state.id)
    assert reopened.state == state
    saved = [e for e in history if e.type != "token_delta"]
    assert dumped(reopened.engine.events.history) == dumped(saved)
    assert reopened.engine.events.closed
    assert reopened.state.messages[0].model == "fake/fake-model"
    assert reopened.state.messages[0].seconds is not None

    async def replay_after(seq):
        return [e.seq async for e in reopened.engine.events.subscribe(after=seq)]

    middle = saved[len(saved) // 2].seq
    assert asyncio.run(replay_after(middle)) == [e.seq for e in saved if e.seq > middle]
    asyncio.run(manager.shutdown())


def test_a_discussion_cut_off_by_a_restart_ends_as_stopped(tmp_path):
    path = tmp_path / "moot.db"
    store = SessionStore(path)
    state = SessionState.new(config())
    state.status = SessionStatus.RUNNING
    store.save(state)
    events = EventStream()
    events.listen(store.recorder(state))
    events.emit(SessionStarted(session_id=state.id))
    events.emit(TurnStarted(message_id="m1", speaker_id=state.config.agents[0].id, round_index=0))
    events.emit(TokenDelta(message_id="m1", text="Half a sen"))
    store.close()  # Moot was closed here, mid-turn

    manager = manager_on(path)
    [(listing, _)] = manager.listings()
    assert (listing.status, listing.end_reason) == (SessionStatus.FINISHED, "stopped")
    record = manager.get(state.id)
    types = [e.type for e in record.engine.events.history]
    assert types == ["session_started", "turn_started", "engine_notice", "session_ended"]
    assert [e.seq for e in record.engine.events.history] == [1, 2, 4, 5]  # after the delta
    assert record.state.end_reason == "stopped"
    with pytest.raises(RuntimeError):
        manager.start(state.id)
    asyncio.run(manager.shutdown())


def test_a_discussion_set_up_but_not_started_can_still_start_after_a_restart(tmp_path):
    path = tmp_path / "moot.db"
    first = manager_on(path)
    session_id = first.create(config()).id
    asyncio.run(first.shutdown())

    async def second_run():
        manager = manager_on(path)
        record = manager.get(session_id)
        assert record.state.status is SessionStatus.SETUP
        manager.start(session_id)
        await record.task
        await manager.shutdown()

    asyncio.run(second_run())
    manager = manager_on(path)
    record = manager.get(session_id)
    assert record.state.status is SessionStatus.FINISHED
    assert record.engine.events.history[0].type == "session_started"
    asyncio.run(manager.shutdown())


def test_deleting_removes_it_from_disk(tmp_path):
    path = tmp_path / "moot.db"

    async def scenario():
        manager = manager_on(path)
        state = await run_to_end(manager, config())
        await manager.delete(state.id)
        with pytest.raises(SessionNotFound):
            await manager.delete(state.id)
        await manager.shutdown()
        return state.id

    session_id = asyncio.run(scenario())
    manager = manager_on(path)
    assert manager.listings() == []
    with pytest.raises(SessionNotFound):
        manager.get(session_id)
    rows = sqlite3.connect(path).execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert rows == 0
    asyncio.run(manager.shutdown())


def test_saved_discussions_from_a_newer_moot_are_refused(tmp_path):
    path = tmp_path / "moot.db"
    SessionStore(path).close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(StoreError, match="newer version"):
        SessionStore(path)


def test_the_stream_continues_numbering_after_saved_events_and_survives_a_bad_listener():
    restored = EventStream.restore(
        [SessionStarted(session_id="s", seq=1), TurnStarted(message_id="m", speaker_id="a", seq=4)],
        closed=False,
    )

    def broken(event):
        raise OSError("disk full")

    restored.listen(broken)
    event = restored.emit(SessionStarted(session_id="s"))
    assert event.seq == 5 and restored.last_seq == 5
    with pytest.raises(ValueError):
        EventStream.restore(
            [SessionStarted(session_id="s", seq=2), SessionStarted(session_id="s", seq=2)],
            closed=True,
        )


# ------------------------------------------------------------------ export


def finished_state(tmp_path):
    async def scenario():
        manager = manager_on(tmp_path / "moot.db")
        state = await run_to_end(manager, config(background="Traffic is up 10%."))
        history = manager.get(state.id).engine.events.history
        await manager.shutdown()
        return state, history

    return asyncio.run(scenario())


def test_the_report_reads_like_a_record_of_the_discussion(tmp_path):
    state, _ = finished_state(tmp_path)
    report = export_markdown(state)
    for heading in (
        "# Should cities ban cars?",
        "## Conclusion",
        "**Question for the audience:**",
        "## Polls",
        "## Transcript",
        "### Round 1",
        "## Turn log",
        "## Setup",
        "### Round plan",
    ):
        assert heading in report, heading
    assert "**Panel:** Ava (Advocate), Ben (Critic)" in report
    assert "> Traffic is up 10%." in report
    assert "| fake/fake-model |" in report
    assert "### After the rounds" in report  # the conclusion comes after the last round
    long = state.model_copy(
        update={
            "config": state.config.model_copy(
                update={"topic": "Should cities ban private cars from their centres?"}
            )
        }
    )
    assert export_filename(long, "md").endswith("-should-cities-ban-private-cars-from-their.md")
    conclusion = report.split("## Conclusion")[1].split("##")[0]
    assert "Question for the audience:" not in conclusion.split("**Question")[0]


def test_the_json_export_holds_everything(tmp_path):
    state, history = finished_state(tmp_path)
    document = json.loads(export_json(state, history))
    assert document["moot_export"] == 1
    assert SessionState.model_validate(document["session"]) == state
    types = [e["type"] for e in document["events"]]
    assert "token_delta" not in types and types[0] == "session_started"
    assert (
        export_filename(state, "md")
        == f"moot-{state.created_at:%Y-%m-%d}-should-cities-ban-cars.md"
    )


def test_the_api_lists_saved_discussions_and_downloads_them(tmp_path):
    path = tmp_path / "moot.db"
    state, _ = finished_state(tmp_path)
    with TestClient(create_app(manager_on(path)), base_url="http://127.0.0.1") as client:
        [summary] = client.get("/api/sessions").json()
        assert (summary["id"], summary["status"], summary["end_reason"]) == (
            state.id,
            "finished",
            "completed",
        )
        report = client.get(f"/api/sessions/{state.id}/export")
        assert report.status_code == 200
        assert report.headers["content-type"].startswith("text/markdown")
        assert "attachment" in report.headers["content-disposition"]
        assert report.text.startswith("# Should cities ban cars?")
        everything = client.get(f"/api/sessions/{state.id}/export?format=json")
        assert everything.json()["session"]["id"] == state.id
        assert client.delete(f"/api/sessions/{state.id}").status_code == 204
        assert client.delete(f"/api/sessions/{state.id}").status_code == 404
        assert client.get("/api/sessions").json() == []
