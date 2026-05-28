"""Tests for vivify/kernel/feature_states.py — state machine validation."""
from __future__ import annotations

import pytest

from vivify.kernel.feature_states import (
    ALL_STATES,
    ACTIVE_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    FeatureStateMachine,
    InvalidTransitionError,
    get_processable_statuses,
    get_status_phase,
)


# ── valid transitions (parametrized) ─────────────────────────────────────────

_VALID_TRANSITIONS = [
    ("pending", "evaluating"),
    ("evaluating", "approved"),
    ("evaluating", "rejected"),
    ("approved", "developing"),
    ("approved", "rejected"),
    ("developing", "deployed"),
    ("developing", "deployed_with_issues"),
    ("developing", "approved"),
    ("deployed", "verifying"),
    ("deployed", "deployed_with_issues"),
    ("verifying", "verified"),
    ("verifying", "deployed_with_issues"),
    ("deployed_with_issues", "approved"),
    ("deployed_with_issues", "rejected"),
]


@pytest.mark.parametrize("src,dst", _VALID_TRANSITIONS)
def test_can_transition_valid(src, dst):
    assert FeatureStateMachine.can_transition(src, dst) is True


@pytest.mark.parametrize("src,dst", _VALID_TRANSITIONS)
def test_validate_transition_valid_no_raise(src, dst):
    # should not raise
    FeatureStateMachine.validate_transition(src, dst)


# ── invalid transitions (parametrized) ────────────────────────────────────────

_INVALID_TRANSITIONS = [
    ("pending", "approved"),
    ("pending", "developing"),
    ("pending", "rejected"),
    ("evaluating", "developing"),
    ("evaluating", "deployed"),
    ("approved", "verifying"),
    ("approved", "verified"),
    ("developing", "verified"),
    ("developing", "verifying"),
    ("deployed", "approved"),
    ("deployed", "rejected"),
    ("verifying", "approved"),
    ("verifying", "developing"),
    ("verified", "pending"),
    ("verified", "approved"),
    ("rejected", "pending"),
    ("rejected", "approved"),
    ("deployed_with_issues", "developing"),
    ("deployed_with_issues", "verified"),
]


@pytest.mark.parametrize("src,dst", _INVALID_TRANSITIONS)
def test_can_transition_invalid(src, dst):
    assert FeatureStateMachine.can_transition(src, dst) is False


@pytest.mark.parametrize("src,dst", _INVALID_TRANSITIONS)
def test_validate_transition_invalid_raises(src, dst):
    with pytest.raises(InvalidTransitionError) as exc_info:
        FeatureStateMachine.validate_transition(src, dst)
    assert exc_info.value.current == src
    assert exc_info.value.target == dst


# ── get_allowed_transitions ───────────────────────────────────────────────────

@pytest.mark.parametrize("state,expected", [
    ("pending", ["evaluating"]),
    ("evaluating", ["approved", "rejected"]),
    ("approved", ["developing", "rejected"]),
    ("developing", ["deployed", "deployed_with_issues", "approved"]),
    ("deployed", ["verifying", "deployed_with_issues"]),
    ("verifying", ["verified", "deployed_with_issues"]),
    ("verified", []),
    ("rejected", []),
    ("deployed_with_issues", ["approved", "rejected"]),
])
def test_get_allowed_transitions(state, expected):
    assert FeatureStateMachine.get_allowed_transitions(state) == expected


# ── is_terminal ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", ["verified", "rejected"])
def test_is_terminal_true(state):
    assert FeatureStateMachine.is_terminal(state) is True


@pytest.mark.parametrize("state", [
    "pending", "evaluating", "approved", "developing",
    "deployed", "verifying", "deployed_with_issues",
])
def test_is_terminal_false(state):
    assert FeatureStateMachine.is_terminal(state) is False


# ── is_active ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", [
    "pending", "evaluating", "approved", "developing",
    "deployed", "verifying", "deployed_with_issues",
])
def test_is_active_true(state):
    assert FeatureStateMachine.is_active(state) is True


@pytest.mark.parametrize("state", ["verified", "rejected"])
def test_is_active_false(state):
    assert FeatureStateMachine.is_active(state) is False


# ── get_processable_statuses ──────────────────────────────────────────────────

def test_get_processable_statuses_returns_sorted_active():
    result = get_processable_statuses()
    assert isinstance(result, list)
    assert result == sorted(ACTIVE_STATES)
    # All processable should be in ACTIVE_STATES
    for s in result:
        assert s in ACTIVE_STATES
    # None should be terminal
    for s in result:
        assert s not in TERMINAL_STATES


# ── get_status_phase ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,phase", [
    ("pending", "queue"),
    ("evaluating", "evaluation"),
    ("approved", "ready"),
    ("developing", "development"),
    ("deployed", "deployment"),
    ("deployed_with_issues", "recovery"),
    ("verifying", "verification"),
    ("verified", "complete"),
    ("rejected", "complete"),
])
def test_get_status_phase(status, phase):
    assert get_status_phase(status) == phase


def test_get_status_phase_unknown():
    assert get_status_phase("nonexistent_state") == "unknown"


# ── boundary: unknown state ───────────────────────────────────────────────────

def test_can_transition_unknown_state_returns_false():
    assert FeatureStateMachine.can_transition("unknown_state", "pending") is False


def test_get_allowed_transitions_unknown_state():
    assert FeatureStateMachine.get_allowed_transitions("unknown_state") == []


def test_is_terminal_unknown_state():
    assert FeatureStateMachine.is_terminal("unknown_state") is False


def test_is_active_unknown_state():
    assert FeatureStateMachine.is_active("unknown_state") is False


# ── module-level constants consistency ────────────────────────────────────────

def test_all_states_equals_transitions_keys():
    assert ALL_STATES == set(TRANSITIONS.keys())


def test_terminal_states_are_subset_of_all():
    assert TERMINAL_STATES.issubset(ALL_STATES)


def test_active_plus_terminal_equals_all():
    assert ACTIVE_STATES | TERMINAL_STATES == ALL_STATES
    assert ACTIVE_STATES & TERMINAL_STATES == set()
