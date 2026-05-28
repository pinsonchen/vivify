"""Tests for vivify/harness/doom_loop.py — DoomLoopDetector."""
from __future__ import annotations

import pytest

from vivify.harness.doom_loop import DoomLoopDetector


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def detector():
    """Detector with window=5 and threshold=3 for easier testing."""
    return DoomLoopDetector(window_size=5, threshold=3)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestDoomLoopDetection:
    """Tests for doom-loop detection logic."""

    def test_no_loop_below_threshold(self, detector):
        """Not enough repetitions does not trigger."""
        detector.record_action("fix_issue", "abc123", "agent_fix")
        detector.record_action("fix_issue", "abc123", "agent_fix")
        assert detector.is_looping() is False

    def test_loop_at_threshold(self, detector):
        """Exactly at threshold triggers loop detection."""
        for _ in range(3):
            detector.record_action("fix_issue", "abc123", "agent_fix")
        assert detector.is_looping() is True

    def test_loop_above_threshold(self, detector):
        """Above threshold also triggers."""
        for _ in range(5):
            detector.record_action("fix_issue", "abc123", "agent_fix")
        assert detector.is_looping() is True

    def test_different_fingerprints_no_loop(self, detector):
        """Different actions do not trigger doom-loop."""
        detector.record_action("fix_issue", "aaa", "agent_fix")
        detector.record_action("develop_feature", "bbb", "agent_fix")
        detector.record_action("fix_issue", "ccc", "direct_fix")
        detector.record_action("fix_issue", "ddd", "agent_fix")
        detector.record_action("develop_feature", "eee", "agent_fix")
        assert detector.is_looping() is False

    def test_window_sliding(self):
        """Old actions outside the window do not count."""
        detector = DoomLoopDetector(window_size=4, threshold=3)
        # Fill with 2 of same fingerprint
        detector.record_action("fix_issue", "abc", "agent_fix")
        detector.record_action("fix_issue", "abc", "agent_fix")
        # Add 3 different ones to push old ones partially out of window
        detector.record_action("develop_feature", "x1", "agent_fix")
        detector.record_action("develop_feature", "x2", "agent_fix")
        detector.record_action("develop_feature", "x3", "agent_fix")
        # Only the last 4 are in window; first one is outside
        # Window: [abc, x1, x2, x3] — actually let's check properly
        # With window=4, the window is last 4: x2's action is #4 from end? 
        # History is [fix_abc, fix_abc, dev_x1, dev_x2, dev_x3]
        # Window (last 4): [fix_abc, dev_x1, dev_x2, dev_x3]
        # fix_abc appears only 1 time in window → no loop
        assert detector.is_looping() is False


class TestReset:
    """Tests for reset method."""

    def test_reset_clears_history(self, detector):
        """After reset, loop is no longer detected."""
        for _ in range(3):
            detector.record_action("fix_issue", "abc123", "agent_fix")
        assert detector.is_looping() is True
        detector.reset()
        assert detector.is_looping() is False


class TestEscapeStrategy:
    """Tests for get_escape_strategy."""

    def test_get_escape_strategy_format(self, detector):
        """Escape strategy contains key information."""
        for _ in range(3):
            detector.record_action("fix_issue", "abc123", "agent_fix")
        strategy = detector.get_escape_strategy()
        assert "DOOM-LOOP DETECTED" in strategy
        assert "fingerprint=" in strategy
        assert "escape strategy" in strategy.lower()


class TestGetLoopFingerprint:
    """Tests for get_loop_fingerprint."""

    def test_get_loop_fingerprint(self, detector):
        """Returns the fingerprint causing the loop."""
        for _ in range(3):
            detector.record_action("fix_issue", "abc123", "agent_fix")
        fp = detector.get_loop_fingerprint()
        assert fp is not None
        assert len(fp) == 8  # MD5 truncated to 8 chars


class TestLoopCount:
    """Tests for loop_count property."""

    def test_loop_count_increments(self, detector):
        """loop_count increments each time is_looping() returns True."""
        assert detector.loop_count == 0
        for _ in range(3):
            detector.record_action("fix_issue", "abc123", "agent_fix")
        detector.is_looping()  # triggers → count=1
        assert detector.loop_count == 1
        detector.is_looping()  # triggers again → count=2
        assert detector.loop_count == 2


class TestHistoryBounded:
    """Tests for history bounding."""

    def test_history_bounded(self):
        """History never exceeds 2x window_size."""
        detector = DoomLoopDetector(window_size=5, threshold=3)
        # Record many actions
        for i in range(50):
            detector.record_action("fix_issue", f"hash_{i}", "agent_fix")
        # Access internal history
        assert len(detector._history) <= 10  # 2 * window_size
