"""Tests for the server: the REST API, the live event stream and the local-only guard."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fakes import FakeProvider
from fastapi.testclient import TestClient

from moot import cli
from moot.core import ModelRef, Persona, SessionConfig
from moot.core.events import SessionStarted
from moot.engine import EventStream
from moot.providers import ProviderConnectionError
from moot.server import SessionManager, create_app
from moot.server.sse import event_source

MODEL = {"provider": "fake", "model": "fake-model"}


@pytest.fixture
def client():
    manager = SessionManager(provider_factory=lambda preset_id: FakeProvider())
    with TestClient(create_app(manager), base_url="http://127.0.0.1") as test_client:
        yield test_client


def demo(client, **extra):
    response = client.post("/api/demo", json={"topic": "Should we test?", **MODEL, **extra})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def wait_for(client, session_id, predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = client.get(f"/api/sessions/{session_id}").json()
        if predicate(view["summary"]):
            return view
        time.sleep(0.01)
    raise AssertionError("the session did not get there in time")


def finished(summary):
    return summary["status"] == "finished"


def read_events(client, session_id, **kwargs):
    """Parse the event stream of a finished session (it ends after the replay)."""
    events, current = [], {}
    with client.stream("GET", f"/api/sessions/{session_id}/events", **kwargs) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if not line:
                if "data" in current:
                    events.append(current)
                current = {}
            elif not line.startswith(":"):
                key, _, value = line.partition(": ")
                current[key] = value
    return events


# ------------------------------------------------------------------ general


def test_health_home_and_test_page(client):
    assert client.get("/api/health").json()["status"] == "ok"
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 200 and '<div id="root">' in home.text  # the built app
    assert client.get("/favicon.svg").status_code == 200
    page = client.get("/dev")
    assert page.status_code == 200 and "server test page" in page.text
    assert client.get("/openapi.json").json()["info"]["title"] == "Moot"


def test_providers_say_whether_a_key_is_set_but_never_send_it(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret-value")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.get("/api/providers")
    providers = {p["id"]: p for p in response.json()}
    assert providers["openrouter"]["key_set"] is True
    assert providers["openai"]["key_set"] is False
    assert providers["lmstudio"]["local"] is True
    assert "sk-secret-value" not in response.text


def test_models_of_a_provider(client):
    assert client.get("/api/providers/fake/models").json() == [
        {"id": "fake-model", "name": None, "context_length": None}
    ]


def test_models_errors():
    class Broken(FakeProvider):
        async def list_models(self):
            raise ProviderConnectionError("LM Studio", "not running")

    with TestClient(
        create_app(SessionManager(provider_factory=lambda preset_id: Broken())),
        base_url="http://127.0.0.1",
    ) as broken:
        response = broken.get("/api/providers/lmstudio/models")
        assert response.status_code == 502 and "not running" in response.json()["detail"]
    with TestClient(create_app(), base_url="http://127.0.0.1") as real:
        assert real.get("/api/providers/nope/models").status_code == 400


def test_library(client):
    library = client.get("/api/library").json()
    assert [p["name"] for p in library["personas"]][0] == "Skeptic"
    assert library["templates"]["classic_debate"]["name"] == "Classic debate"
    assert "answer_first" in library["rule_presets"]
    assert library["global_rules"]


# ------------------------------------------------------------------ sessions


def test_create_list_and_get_a_session(client):
    config = SessionConfig(
        topic="Should cities ban cars?",
        agents=[Persona(name="Ava"), Persona(name="Ben")],
        default_model=ModelRef(**MODEL),
    )
    created = client.post("/api/sessions", json=config.model_dump(mode="json"))
    assert created.status_code == 201
    summary = created.json()
    assert (summary["status"], summary["agents"], summary["rounds"]) == ("setup", ["Ava", "Ben"], 5)

    assert [s["id"] for s in client.get("/api/sessions").json()] == [summary["id"]]
    view = client.get(f"/api/sessions/{summary['id']}").json()
    assert view["state"]["config"]["topic"] == "Should cities ban cars?"
    assert view["state"]["graph"]["nodes"]  # the topic node


def test_bad_setups_are_refused():
    with TestClient(create_app(), base_url="http://127.0.0.1") as real:
        unknown = real.post("/api/demo", json={"provider": "nope", "model": "x"})
        assert unknown.status_code == 400 and "Unknown provider" in unknown.json()["detail"]
        one_agent = {"topic": "T", "agents": [{"name": "Ava"}], "default_model": MODEL}
        assert real.post("/api/sessions", json=one_agent).status_code == 422


def test_a_demo_runs_and_its_events_replay(client):
    session_id = demo(client)
    started = client.post(f"/api/sessions/{session_id}/start")
    assert started.status_code == 200

    view = wait_for(client, session_id, finished)
    assert view["state"]["audience_question"] == "What would change your mind?"
    assert view["state"]["polls"][0]["status"] == "closed"

    events = read_events(client, session_id)
    assert events[0]["event"] == "session_started" and events[0]["id"] == "1"
    assert events[-1]["event"] == "session_ended"
    assert all(json.loads(e["data"])["type"] == e["event"] for e in events)
    assert [int(e["id"]) for e in events] == list(range(1, len(events) + 1))

    assert read_events(client, session_id, params={"after": 5})[0]["id"] == "6"
    resumed = read_events(client, session_id, headers={"Last-Event-ID": str(len(events) - 1)})
    assert [e["event"] for e in resumed] == ["session_ended"]


def test_commands_reach_the_engine(client):
    session_id = demo(client)
    base = f"/api/sessions/{session_id}"
    assert client.post(f"{base}/messages", json={"text": "@Ava why?"}).status_code == 202
    assert client.post(f"{base}/polls", json={}).status_code == 202
    poll = client.post(f"{base}/polls", json={"question": "Fair?", "options": ["Yes", "No"]})
    assert poll.status_code == 202
    assert (
        client.post(f"{base}/reminders", json={"agent": "Ben", "text": "Be brief."}).status_code
        == 202
    )
    bad = client.post(f"{base}/floor", json={"agent": "Zed"})
    assert bad.status_code == 400 and "Zed" in bad.json()["detail"]
    assert (
        client.post(f"{base}/polls", json={"question": "Q", "options": ["Only"]}).status_code == 400
    )

    client.post(f"{base}/start")
    state = wait_for(client, session_id, finished)["state"]
    assert any(m["speaker_kind"] == "user" and m["text"] == "@Ava why?" for m in state["messages"])
    assert [p["question"] for p in state["polls"]][:2] == ["Who made the strongest case?", "Fair?"]
    assert client.post(f"{base}/start").status_code == 409
    assert client.post(f"{base}/messages", json={"text": "Too late"}).status_code == 409


def test_you_can_moderate_over_the_api(client):
    session_id = demo(client, moderator={"kind": "human", "display_name": "Sina", "role": "Judge"})
    base = f"/api/sessions/{session_id}"
    client.post(f"{base}/start")
    floors = 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        summary = client.get(base).json()["summary"]
        if summary["status"] == "finished":
            break
        if summary["waiting_for"] in ("start_round", "finish"):
            client.post(f"{base}/next-round")
        elif summary["waiting_for"] == "next_speaker":
            if floors < 3:
                client.post(f"{base}/floor", json={"agent": ["Ben", "Cleo", "Ava"][floors]})
                floors += 1
            else:
                client.post(f"{base}/end-round")
        time.sleep(0.01)
    state = client.get(base).json()["state"]
    assert state["status"] == "finished"
    challenge = [
        m for m in state["messages"] if m["round_index"] == 1 and m["speaker_kind"] == "agent"
    ]
    names = {a["id"]: a["name"] for a in state["config"]["agents"]}
    assert [names[m["speaker_id"]] for m in challenge] == ["Ben", "Cleo", "Ava"]
    assert state["report"] is None  # no AI conclusion when you moderate


def test_unknown_and_deleted_sessions(client):
    assert client.get("/api/sessions/ses_nope").status_code == 404
    assert client.post("/api/sessions/ses_nope/start").status_code == 404

    waiting = demo(client, moderator={"kind": "human", "display_name": "Sina"})
    client.post(f"/api/sessions/{waiting}/start")
    wait_for(client, waiting, lambda s: s["waiting_for"] == "start_round")
    assert client.delete(f"/api/sessions/{waiting}").status_code == 204  # ends it at once
    assert client.get(f"/api/sessions/{waiting}").status_code == 404
    assert client.get("/api/sessions").json() == []


def test_watching_alliances_and_the_argument_map(client):
    session_id = demo(client)
    client.post(f"/api/sessions/{session_id}/start")
    wait_for(client, session_id, finished)
    monitor = client.get(f"/api/sessions/{session_id}/alliances").json()
    assert {"pairs", "sides", "alliances", "relations", "mismatches"} <= set(monitor)
    assert len(monitor["pairs"]) == 3
    assert client.get(f"/api/sessions/{session_id}/alliances?up_to_round=0").status_code == 200
    sections = client.get(f"/api/sessions/{session_id}/argument-map").json()
    assert sections[0]["title"] and sections[0]["items"]


def test_a_crashing_session_reports_its_error():
    class Crashing(FakeProvider):
        async def stream(self, request):
            raise KeyError("boom")
            yield  # pragma: no cover

    manager = SessionManager(provider_factory=lambda preset_id: Crashing())
    with TestClient(create_app(manager), base_url="http://127.0.0.1") as crashing:
        session_id = demo(crashing)
        crashing.post(f"/api/sessions/{session_id}/start")
        summary = wait_for(crashing, session_id, lambda s: s["error"])["summary"]
        assert "KeyError" in summary["error"]


# ------------------------------------------------------------------ local only


def test_only_this_computer_and_its_own_pages_may_call(client):
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    cross_site = client.post("/api/demo", json=MODEL, headers={"Origin": "https://evil.example"})
    assert cross_site.status_code == 403
    same_site = client.get("/api/health", headers={"Origin": "http://127.0.0.1"})
    assert same_site.status_code == 200
    assert client.get("/api/health", headers={"Origin": "null"}).status_code == 403


def test_hosts_and_origins_can_be_opened_up():
    app = create_app(allowed_hosts=None, allowed_origins=["http://localhost:5173"])
    with TestClient(app, base_url="http://my-pc.lan") as lan:
        assert lan.get("/api/health").status_code == 200
        ui = lan.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert ui.status_code == 200


# ------------------------------------------------------------------ the stream


def test_event_source_keeps_idle_connections_open_and_ends_with_the_discussion():
    async def scenario():
        stream = EventStream()
        chunks = event_source(stream, heartbeat=0.01)
        first = await anext(chunks)
        ping = await anext(chunks)
        stream.emit(SessionStarted(session_id="ses_1"))
        stream.close()
        rest = [chunk async for chunk in chunks]
        return first, ping, rest

    first, ping, rest = asyncio.run(scenario())
    assert first.startswith("retry: ")
    assert ping == ": ping\n\n"
    events = [chunk for chunk in rest if chunk.startswith("id: ")]
    assert len(events) == 1 and "event: session_started" in events[0]


# ------------------------------------------------------------------ moot serve


def test_serve_command(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)

    assert cli.main(["serve"]) == 0
    app, kwargs = calls[0]
    assert (kwargs["host"], kwargs["port"]) == ("127.0.0.1", 8765)
    assert "Moot is running at http://127.0.0.1:8765" in capsys.readouterr().out

    cli.main(["serve", "--host", "0.0.0.0", "--port", "9000"])
    assert calls[1][1]["port"] == 9000
    assert "Warning: anyone who can reach this address" in capsys.readouterr().out
    assert opened == []


# ------------------------------------------------------------------ the app


def test_the_built_app_is_served_when_present(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Moot app</title>", "utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('moot')", "utf-8")
    (tmp_path / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", "utf-8")
    with TestClient(create_app(static_dir=tmp_path), base_url="http://127.0.0.1") as app:
        assert "Moot app" in app.get("/").text
        script = app.get("/assets/app.js")
        assert script.status_code == 200 and "moot" in script.text
        assert app.get("/favicon.svg").headers["content-type"].startswith("image/svg")

    with TestClient(create_app(static_dir=tmp_path / "none"), base_url="http://127.0.0.1") as bare:
        assert bare.get("/", follow_redirects=False).headers["location"] == "/dev"
        assert bare.get("/favicon.svg").status_code == 404
