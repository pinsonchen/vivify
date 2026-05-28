"""Doom-loop detection for AI agent repeated ineffective operations."""
from __future__ import annotations

import hashlib
import logging
from collections import Counter

logger = logging.getLogger(__name__)


class DoomLoopDetector:
    """Detect when an agent is stuck in a repetitive loop of ineffective actions.

    Uses a sliding window of action fingerprints to detect patterns.
    A fingerprint is derived from: ``category + issue_hash + action_type``.

    When the same fingerprint appears ``>= threshold`` times within the
    most recent ``window_size`` actions, a doom-loop is detected and an
    escape strategy is triggered.
    """

    def __init__(self, window_size: int = 10, threshold: int = 3):
        """Initialize detector.

        Args:
            window_size: Number of recent actions to examine.
            threshold: How many repetitions trigger doom-loop detection.
        """
        self._history: list[str] = []
        self.window_size = window_size
        self.threshold = threshold
        self._loop_count: int = 0  # total doom-loops detected this session

    @property
    def loop_count(self) -> int:
        """Total number of doom-loops detected in this session."""
        return self._loop_count

    def record_action(self, category: str, issue_hash: str, action_type: str) -> None:
        """Record an agent action fingerprint.

        Args:
            category: Action category (e.g. ``"fix_issue"``, ``"develop_feature"``).
            issue_hash: Hash of the issue being worked on.
            action_type: Type of action (e.g. ``"agent_fix"``, ``"direct_fix"``).
        """
        fingerprint = self._compute_fingerprint(category, issue_hash, action_type)
        self._history.append(fingerprint)

        # Keep history bounded to 2x window for memory efficiency
        max_history = self.window_size * 2
        if len(self._history) > max_history:
            self._history = self._history[-max_history:]

    def is_looping(self) -> bool:
        """Check if the agent is in a doom-loop.

        Returns:
            True if any fingerprint appears ``>= threshold`` times in the
            most recent ``window_size`` actions.
        """
        if len(self._history) < self.threshold:
            return False

        window = self._history[-self.window_size:]
        counts = Counter(window)

        for fingerprint, count in counts.items():
            if count >= self.threshold:
                logger.warning(
                    "Doom-loop detected: fingerprint %s appeared %d times "
                    "in last %d actions (threshold=%d)",
                    fingerprint, count, len(window), self.threshold,
                )
                self._loop_count += 1
                return True

        return False

    def get_escape_strategy(self) -> str:
        """Get escape strategy message when doom-loop is detected.

        Returns:
            A structured message describing:
              1. What was detected
              2. The recommended action (skip + cooldown)
              3. Warning for future injection
        """
        window = self._history[-self.window_size:]
        counts = Counter(window)
        most_common = counts.most_common(1)

        if not most_common:
            return ""

        fingerprint, count = most_common[0]

        return (
            f"[DOOM-LOOP DETECTED] The same action pattern (fingerprint={fingerprint}) "
            f"has been repeated {count} times in the last {self.window_size} operations.\n"
            f"Recommended escape strategy:\n"
            f"  1. Skip the current issue and add extended cooldown\n"
            f"  2. Mark issue as 'needs_human_review'\n"
            f"  3. Try a different approach if retrying later\n"
            f"WARNING: Continuing the same approach will not resolve this issue."
        )

    def get_loop_fingerprint(self) -> str | None:
        """Get the fingerprint causing the current doom-loop, if any."""
        if len(self._history) < self.threshold:
            return None

        window = self._history[-self.window_size:]
        counts = Counter(window)

        for fingerprint, count in counts.items():
            if count >= self.threshold:
                return fingerprint
        return None

    def reset(self) -> None:
        """Reset history (e.g. at the start of a new round)."""
        self._history.clear()

    def _compute_fingerprint(self, category: str, issue_hash: str, action_type: str) -> str:
        """Compute action fingerprint from components.

        Returns:
            First 8 chars of MD5 hash of ``"{category}:{issue_hash}:{action_type}"``.
        """
        raw = f"{category}:{issue_hash}:{action_type}"
        return hashlib.md5(raw.encode()).hexdigest()[:8]


__all__ = ["DoomLoopDetector"]
