"""Tests for declared alliances."""

from __future__ import annotations

import pytest

from moot.core import Alliance, AllianceError, AllianceStatus


def test_proposal_then_acceptance_makes_it_active():
    alliance = Alliance.propose("ava", ["ben", "cleo"], name="Reformers")
    assert alliance.status is AllianceStatus.PROPOSED
    assert alliance.members == ["ava"]
    assert not alliance.is_member("ava")

    alliance.accept("ben")
    assert alliance.status is AllianceStatus.ACTIVE
    assert alliance.members == ["ava", "ben"]
    assert alliance.invited == ["cleo"]
    assert alliance.is_member("ben")


def test_everyone_refusing_dissolves_it():
    alliance = Alliance.propose("ava", ["ben", "cleo"])
    alliance.refuse("ben")
    assert alliance.status is AllianceStatus.PROPOSED
    alliance.refuse("cleo")
    assert alliance.status is AllianceStatus.DISSOLVED
    assert alliance.refused == ["ben", "cleo"]


def test_leaving_below_two_members_dissolves_it():
    alliance = Alliance.propose("ava", ["ben"])
    alliance.accept("ben")
    alliance.leave("ben")
    assert alliance.status is AllianceStatus.DISSOLVED
    with pytest.raises(AllianceError, match="dissolved"):
        alliance.leave("ava")


def test_three_member_alliance_survives_one_leaving():
    alliance = Alliance.propose("ava", ["ben", "cleo"])
    alliance.accept("ben")
    alliance.accept("cleo")
    alliance.leave("ava")
    assert alliance.status is AllianceStatus.ACTIVE
    assert alliance.members == ["ben", "cleo"]


def test_invalid_alliance_actions():
    with pytest.raises(AllianceError):
        Alliance.propose("ava", [])
    with pytest.raises(AllianceError):
        Alliance.propose("ava", ["ava", "ben"])
    alliance = Alliance.propose("ava", ["ben"])
    with pytest.raises(AllianceError, match="not invited"):
        alliance.accept("cleo")
    with pytest.raises(AllianceError, match="not a member"):
        alliance.leave("cleo")


def test_duplicate_invitees_are_merged():
    assert Alliance.propose("ava", ["ben", "ben"]).invited == ["ben"]
