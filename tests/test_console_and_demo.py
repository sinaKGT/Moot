"""Tests for the console renderer, the demo debate and the `moot debate` command."""

from __future__ import annotations

import asyncio
import io

from fakes import FakeProvider

from moot import cli
from moot.cli import main
from moot.core import ModelRef, SessionState
from moot.demo import DEMO_TOPIC, demo_config, run_in_console
from moot.engine import ProviderPool
from moot.envfile import load_env_file

MODEL = ModelRef(provider="fake", model="fake-model")


def test_demo_config_has_two_opposing_agents():
    config = demo_config("Test topic", MODEL)
    assert [a.name for a in config.agents] == ["Ava", "Ben"]
    assert "in favour" in config.agents[0].viewpoint
    assert "against" in config.agents[1].viewpoint
    assert len(config.round_plan) == 2


def test_run_in_console_prints_the_discussion():
    state = SessionState.new(demo_config("Should we test?", MODEL))
    out = io.StringIO()
    pool = ProviderPool(factory=lambda preset_id: FakeProvider())

    reason = asyncio.run(run_in_console(state, providers=pool, out=out, color=False))

    text = out.getvalue()
    assert reason == "completed"
    assert "Topic: Should we test?" in text
    assert "Participants: Ava (Advocate), Ben (Critic)" in text
    assert "── Round 1 of 2: Opening statements ──" in text
    assert "Ava: Ava round 1" in text
    assert "Ben: Ben round 2" in text
    assert "[10 in / 5 out tokens]" in text
    assert text.rstrip().endswith("Discussion finished.")
    assert "\x1b[" not in text


def test_colour_output_uses_agent_colours():
    state = SessionState.new(demo_config("Colours?", MODEL))
    out = io.StringIO()
    pool = ProviderPool(factory=lambda preset_id: FakeProvider())
    asyncio.run(run_in_console(state, providers=pool, out=out, color=True))
    assert "\x1b[38;2;34;211;238m" in out.getvalue()  # Ava's cyan


def test_debate_command(monkeypatch, capsys):
    monkeypatch.setattr("moot.engine.pool.create_provider", lambda preset_id: FakeProvider())
    assert main(["debate", "--no-color", "--topic", "Is testing worth it?"]) == 0
    out = capsys.readouterr().out
    assert "Topic: Is testing worth it?" in out
    assert "Discussion finished." in out


def test_debate_command_defaults_and_errors(monkeypatch, capsys):
    seen = []

    def fake_run(state, **kwargs):
        seen.append(state.config)

        async def done():
            return "completed"

        return done()

    monkeypatch.setattr(cli, "run_in_console", fake_run)
    assert main(["debate"]) == 0
    assert seen[0].topic == DEMO_TOPIC
    assert seen[0].default_model.provider == "openrouter"

    assert main(["debate", "--provider", "lmstudio"]) == 2
    assert "--model" in capsys.readouterr().err
    assert main(["debate", "--provider", "nope"]) == 2


def test_env_file_loading(tmp_path, monkeypatch):
    monkeypatch.delenv("MOOT_A", raising=False)
    monkeypatch.delenv("MOOT_B", raising=False)
    monkeypatch.setenv("MOOT_KEEP", "original")
    env = tmp_path / ".env"
    env.write_text(
        '# comment\n\nMOOT_A=plain\nMOOT_B="quoted value"\nMOOT_KEEP=changed\nnot a setting\n',
        encoding="utf-8",
    )

    assert load_env_file(env) == ["MOOT_A", "MOOT_B"]
    import os

    assert os.environ["MOOT_A"] == "plain"
    assert os.environ["MOOT_B"] == "quoted value"
    assert os.environ["MOOT_KEEP"] == "original"
    assert load_env_file(tmp_path / "missing.env") == []
