"""Tests for finding @mentions."""

from __future__ import annotations

from moot.core import Persona
from moot.engine.mentions import find_mentions

AVA, BEN, EXPERT = Persona(name="Ava"), Persona(name="Ben"), Persona(name="Domain Expert")
AGENTS = [AVA, BEN, EXPERT]


def test_mentions_in_order_of_appearance():
    text = "@Ben what do you think? And @ava, your evidence?"
    assert find_mentions(text, AGENTS) == [BEN.id, AVA.id]


def test_longer_names_win_and_duplicates_collapse():
    text = "@Domain Expert please answer. Thanks @domain expert!"
    assert find_mentions(text, AGENTS) == [EXPERT.id]


def test_not_a_mention():
    assert find_mentions("mail me at x@Ben.org or ask Benjamin", AGENTS) == []
    assert find_mentions("@Benjamin is not Ben", AGENTS) == []


def test_speaker_is_excluded():
    assert find_mentions("As I, @Ava, said: @Ben?", AGENTS, exclude=AVA.id) == [BEN.id]
