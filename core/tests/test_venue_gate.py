"""Тесты объединённого гейта активных действий (спец. §7.1-§7.3)."""
from __future__ import annotations

import pytest

from app.models.enums import VenueMode
from app.services.venue_gate import ActionBlocked, assert_active_action_allowed


def _call(**over):
    args = dict(
        target="example.com",
        venue_mode=VenueMode.attack_box,
        authorized=True,
        scope_confirmed=True,
        allow=["example.com"],
        deny=[],
    )
    args.update(over)
    return assert_active_action_allowed(**args)


def test_allowed_when_all_conditions_met():
    assert _call().allowed is True


def test_analysis_only_blocks_active_action():
    with pytest.raises(ActionBlocked):
        _call(venue_mode=VenueMode.analysis_only)


def test_unauthorized_engagement_blocked():
    with pytest.raises(ActionBlocked):
        _call(authorized=False)


def test_unconfirmed_scope_blocked():
    with pytest.raises(ActionBlocked):
        _call(scope_confirmed=False)


def test_out_of_scope_target_blocked():
    with pytest.raises(ActionBlocked):
        _call(target="evil.com")


def test_this_machine_requires_gate_too():
    # this_machine — тоже активная площадка, гейт применяется.
    with pytest.raises(ActionBlocked):
        _call(venue_mode=VenueMode.this_machine, authorized=False)
