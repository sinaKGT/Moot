"""The Team Designer: a goal becomes a tidy panel, checked for balance by a second pass."""

from __future__ import annotations

import asyncio
import json

import pytest
from fakes import FakeProvider
from fastapi.testclient import TestClient

from moot.engine.designer import DesignError, TeamDesigner
from moot.server import SessionManager, create_app


def agent(name, viewpoint="You argue a view.", **extra):
    return {"name": name, "role": "Role", "viewpoint": viewpoint, **extra}


def design_answer(**extra):
    return {
        "topic": "Which city should we open our next office in?",
        "format": "decision",
        "options": ["Lisbon", "Berlin", " lisbon ", "Stay remote."],
        "agents": [
            agent("Ines", "You argue for Lisbon.", persuadability=0.3),
            agent("Jonas", "You argue for Berlin.", persuadability="high"),
            agent("Mira", "You argue for staying remote.", verbosity=7),
        ],
        "rationale": "Each option has a champion.",
        **extra,
    }


def scripted(design, review=None):
    def script(request):
        system = request.messages[0].content
        if system.startswith("You design panels"):
            return [json.dumps(design)]
        if system.startswith("You review discussion panels"):
            return [json.dumps(review or {"balanced": True, "notes": ["Distinct views."]})]
        raise AssertionError("unexpected request")

    return script


def run(provider, **options):
    return asyncio.run(
        TeamDesigner().design("Pick a city for our office", provider, "m", **options)
    )


def test_a_goal_becomes_a_tidy_panel():
    provider = FakeProvider(scripted(design_answer()))
    proposal = run(provider)
    assert proposal.topic == "Which city should we open our next office in?"
    assert proposal.format == "decision"
    assert proposal.options == ["Lisbon", "Berlin", "Stay remote"]  # repeats and dots tidied
    assert [a.name for a in proposal.agents] == ["Ines", "Jonas", "Mira"]
    assert proposal.agents[1].traits.persuadability == 0.5  # unreadable number: middle
    assert proposal.agents[2].traits.verbosity == 1.0  # out of range: clamped
    assert proposal.balance_notes == ["Distinct views."] and not proposal.revised
    assert len(provider.requests) == 2
    design_prompt = provider.requests[0].messages[0].content
    assert '"decision"' in design_prompt and "never a real person" in design_prompt


def test_names_are_made_unique_and_safe_and_unusable_agents_dropped():
    answer = design_answer(
        agents=[
            agent("Ava"),
            agent("ava"),
            agent("@Moderator"),  # would clash with the moderator and @mentions
            agent("Ben\nCarter"),
            agent(""),  # no name
            agent("Cleo", viewpoint=" "),  # no view
        ]
    )
    proposal = run(FakeProvider(scripted(answer)), balance_check=False)
    assert [a.name for a in proposal.agents] == ["Ava", "ava 2", "Moderator 2", "Ben Carter"]
    assert proposal.balance_notes == []


def test_the_requested_number_of_agents_is_respected():
    answer = design_answer(agents=[agent(n) for n in ["A1", "A2", "A3", "A4", "A5"]])
    provider = FakeProvider(scripted(answer))
    proposal = run(provider, agents=3, balance_check=False)
    assert len(proposal.agents) == 3
    assert "exactly 3" in provider.requests[0].messages[0].content
    with pytest.raises(DesignError, match="2 to 8"):
        run(provider, agents=9)


def test_formats_fall_back_sensibly():
    debate = run(
        FakeProvider(scripted(design_answer(format="town hall", options=[]))), balance_check=False
    )
    assert (debate.format, debate.options) == ("classic_debate", [])
    one_option = design_answer(format="decision", options=["Only Lisbon"])
    assert run(FakeProvider(scripted(one_option)), balance_check=False).format == "classic_debate"
    options_but_no_format = design_answer(format="", options=["A", "B"])
    assert (
        run(FakeProvider(scripted(options_but_no_format)), balance_check=False).format == "decision"
    )


def test_the_balance_check_can_revise_the_panel():
    review = {
        "balanced": False,
        "notes": ["Jonas and Mira argue the same thing.", "Nobody speaks for staying remote."],
        "agents": [
            agent("Ines", "You argue for Lisbon."),
            agent("Jonas", "You argue for Berlin."),
            agent("Rafa", "You argue for staying remote: no office at all."),
        ],
    }
    proposal = run(FakeProvider(scripted(design_answer(), review)))
    assert proposal.revised
    assert [a.name for a in proposal.agents] == ["Ines", "Jonas", "Rafa"]
    assert proposal.balance_notes[1] == "Nobody speaks for staying remote."


def test_a_failed_balance_check_keeps_the_first_panel():
    def script(request):
        if request.messages[0].content.startswith("You design panels"):
            return [json.dumps(design_answer())]
        return ["not json at all"]

    proposal = run(FakeProvider(script))
    assert [a.name for a in proposal.agents] == ["Ines", "Jonas", "Mira"]
    assert "check the views yourself" in proposal.balance_notes[0]


def test_an_unusable_design_is_an_error():
    with pytest.raises(DesignError, match="fewer than two"):
        run(FakeProvider(scripted(design_answer(agents=[agent("Solo")]))))
    with pytest.raises(DesignError, match="couldn't be used"):
        run(FakeProvider(lambda request: ["no json"]))


def test_the_api_designs_a_team():
    provider = FakeProvider(scripted(design_answer()))
    app = create_app(SessionManager(provider_factory=lambda preset: provider))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        found = client.post(
            "/api/designer",
            json={"goal": "Pick a city", "provider": "fake", "model": "m", "agents": 3},
        )
        assert found.status_code == 200
        body = found.json()
        assert body["format"] == "decision"
        assert [a["name"] for a in body["agents"]] == ["Ines", "Jonas", "Mira"]
        assert provider.closed
        assert (
            client.post(
                "/api/designer", json={"goal": "", "provider": "fake", "model": "m"}
            ).status_code
            == 422
        )
        broken = FakeProvider(lambda request: ["no json"])
        app.state.manager._provider_factory = lambda preset: broken
        failed = client.post("/api/designer", json={"goal": "x", "provider": "fake", "model": "m"})
        assert failed.status_code == 502 and "couldn't be used" in failed.json()["detail"]
