"""Multi-modal reward signal system — quantified feedback for learning loops.

Implements a four-dimensional reward system:
1. Correctness (40%) — test/lint/type improvements
2. Efficiency (20%) — build time, code reduction, agent turns
3. Stability (25%) — retroactive regression check
4. Elegance (15%) — lint warnings, complexity, dependencies

The reward signals are computed after each fix/develop action and fed back
to the skill capsule layer and epigenetics engine to reinforce high-quality
patterns.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────────
# Data models
# ────────────────────────────────────────────────────────────────────────────────


@dataclass
class RewardSignal:
    """A single reward signal computed after a fix/development action."""

    action_id: str
    timestamp: datetime = field(default_factory=datetime.now)

    # Four dimensions (0.0 = bad, 1.0 = excellent)
    correctness: float = 0.5
    efficiency: float = 0.5
    stability: float = 0.5  # Initially neutral, updated retroactively
    elegance: float = 0.5

    # Weights (configurable)
    _weights: Dict[str, float] = field(default_factory=lambda: {
        "correctness": 0.40,
        "efficiency": 0.20,
        "stability": 0.25,
        "elegance": 0.15,
    })

    @property
    def composite_score(self) -> float:
        """Weighted composite reward score."""
        return (
            self.correctness * self._weights["correctness"]
            + self.efficiency * self._weights["efficiency"]
            + self.stability * self._weights["stability"]
            + self.elegance * self._weights["elegance"]
        )

    @property
    def is_positive(self) -> bool:
        """Whether this is a net positive signal."""
        return self.composite_score >= 0.6

    @property
    def category(self) -> str:
        """Categorize signal strength."""
        score = self.composite_score
        if score >= 0.8:
            return "excellent"
        elif score >= 0.6:
            return "good"
        elif score >= 0.4:
            return "neutral"
        else:
            return "poor"

    def to_dict(self) -> dict:
        """Serialize to dict for logging/storage."""
        return {
            "action_id": self.action_id,
            "timestamp": self.timestamp.isoformat(),
            "correctness": round(self.correctness, 3),
            "efficiency": round(self.efficiency, 3),
            "stability": round(self.stability, 3),
            "elegance": round(self.elegance, 3),
            "composite_score": round(self.composite_score, 3),
            "category": self.category,
            "is_positive": self.is_positive,
        }


# ────────────────────────────────────────────────────────────────────────────────
# Reward Calculator
# ────────────────────────────────────────────────────────────────────────────────


class RewardCalculator:
    """Calculates reward signals from action outcomes."""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self._weights = weights or {
            "correctness": 0.40,
            "efficiency": 0.20,
            "stability": 0.25,
            "elegance": 0.15,
        }

    def calculate_immediate(self, action_result: dict) -> RewardSignal:
        """Calculate immediate reward from action result.

        Called right after a fix/develop action completes.

        action_result expected keys:
            - action_id: str
            - success: bool
            - tests_before: int (pass count before)
            - tests_after: int (pass count after)
            - tests_total: int
            - lint_errors_before: int
            - lint_errors_after: int
            - type_errors_before: int
            - type_errors_after: int
            - build_time_before: float (seconds)
            - build_time_after: float (seconds)
            - agent_turns: int
            - diff_lines_added: int
            - diff_lines_removed: int
            - files_changed: int
            - new_dependencies: int
            - lint_warnings_delta: int (positive = more warnings)
        """
        signal = RewardSignal(
            action_id=action_result.get("action_id", ""),
            _weights=self._weights,
        )

        # Correctness
        signal.correctness = self._calc_correctness(action_result)

        # Efficiency
        signal.efficiency = self._calc_efficiency(action_result)

        # Elegance
        signal.elegance = self._calc_elegance(action_result)

        # Stability starts neutral (updated later by update_stability)
        signal.stability = 0.5

        return signal

    def update_stability(
        self, signal: RewardSignal, stability_data: dict
    ) -> RewardSignal:
        """Retroactively update stability dimension.

        Called N days after the action to check for regressions.

        stability_data expected keys:
            - regressed: bool (same issue recurred?)
            - days_since_fix: int
            - pr_reverted: bool
            - same_probe_hit_count: int (times same probe fired since fix)
        """
        if stability_data.get("pr_reverted", False):
            signal.stability = 0.0
        elif stability_data.get("regressed", False):
            signal.stability = 0.2
        elif stability_data.get("same_probe_hit_count", 0) > 0:
            hits = stability_data["same_probe_hit_count"]
            signal.stability = max(0.3, 0.8 - hits * 0.1)
        else:
            # No regression, stable fix
            days = stability_data.get("days_since_fix", 0)
            signal.stability = min(1.0, 0.6 + days * 0.05)  # Grows with time

        return signal

    def _calc_correctness(self, data: dict) -> float:
        """Calculate correctness reward."""
        if not data.get("success", False):
            return 0.1

        score = 0.5  # Base for success

        # Test improvement
        before = data.get("tests_before", 0)
        after = data.get("tests_after", 0)
        total = data.get("tests_total", 1)
        if total > 0 and after >= before:
            score += 0.2 * (after / total)
        elif after < before:
            score -= 0.3  # Test regression penalty

        # Lint improvement
        lint_before = data.get("lint_errors_before", 0)
        lint_after = data.get("lint_errors_after", 0)
        if lint_after <= lint_before:
            score += 0.15
        else:
            score -= 0.1

        # Type errors
        type_before = data.get("type_errors_before", 0)
        type_after = data.get("type_errors_after", 0)
        if type_after <= type_before:
            score += 0.15
        else:
            score -= 0.1

        return max(0.0, min(1.0, score))

    def _calc_efficiency(self, data: dict) -> float:
        """Calculate efficiency reward."""
        score = 0.5

        # Agent turns (fewer = better)
        turns = data.get("agent_turns", 30)
        if turns <= 5:
            score += 0.3
        elif turns <= 15:
            score += 0.15
        elif turns > 50:
            score -= 0.2

        # Net code reduction is positive
        added = data.get("diff_lines_added", 0)
        removed = data.get("diff_lines_removed", 0)
        if removed > added:  # Net reduction
            score += 0.1

        # Build time
        build_before = data.get("build_time_before", 0)
        build_after = data.get("build_time_after", 0)
        if build_before > 0 and build_after <= build_before:
            score += 0.1

        return max(0.0, min(1.0, score))

    def _calc_elegance(self, data: dict) -> float:
        """Calculate elegance reward."""
        score = 0.6  # Slightly positive base

        # Lint warnings change
        warning_delta = data.get("lint_warnings_delta", 0)
        if warning_delta < 0:  # Reduced warnings
            score += 0.2
        elif warning_delta > 5:
            score -= 0.2

        # File count (fewer changes = more focused)
        files = data.get("files_changed", 1)
        if files <= 3:
            score += 0.1
        elif files > 10:
            score -= 0.15

        # New dependencies penalty
        new_deps = data.get("new_dependencies", 0)
        score -= new_deps * 0.15

        return max(0.0, min(1.0, score))


# ────────────────────────────────────────────────────────────────────────────────
# Reward Aggregator
# ────────────────────────────────────────────────────────────────────────────────


class RewardAggregator:
    """Aggregates reward signals and provides feedback to other systems."""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        max_history: int = 100,
    ):
        self._signals: List[RewardSignal] = []
        self._max_history = max_history
        self._calculator = RewardCalculator(weights=weights)

    @property
    def calculator(self) -> RewardCalculator:
        """Expose the calculator for direct use."""
        return self._calculator

    @property
    def signals(self) -> List[RewardSignal]:
        """Access recorded signals (read-only snapshot)."""
        return list(self._signals)

    def record_action_reward(self, action_result: dict) -> RewardSignal:
        """Record reward for a completed action."""
        signal = self._calculator.calculate_immediate(action_result)
        self._signals.append(signal)
        # Trim history to prevent memory growth
        if len(self._signals) > self._max_history:
            self._signals = self._signals[-self._max_history:]
        logger.info(
            "Reward signal recorded: action=%s score=%.3f category=%s",
            signal.action_id[:12],
            signal.composite_score,
            signal.category,
        )
        return signal

    def get_recent_average(self, n: int = 10) -> Dict[str, float]:
        """Get average reward across recent N signals."""
        recent = self._signals[-n:] if self._signals else []
        if not recent:
            return {
                "correctness": 0.5,
                "efficiency": 0.5,
                "stability": 0.5,
                "elegance": 0.5,
                "composite": 0.5,
            }

        return {
            "correctness": sum(s.correctness for s in recent) / len(recent),
            "efficiency": sum(s.efficiency for s in recent) / len(recent),
            "stability": sum(s.stability for s in recent) / len(recent),
            "elegance": sum(s.elegance for s in recent) / len(recent),
            "composite": sum(s.composite_score for s in recent) / len(recent),
        }

    def get_trend(self, window: int = 20) -> str:
        """Assess reward trend (improving/stable/degrading)."""
        if len(self._signals) < window:
            return "insufficient_data"

        first_half = self._signals[-(window): -window // 2]
        second_half = self._signals[-window // 2:]

        avg_first = sum(s.composite_score for s in first_half) / len(first_half)
        avg_second = sum(s.composite_score for s in second_half) / len(second_half)

        delta = avg_second - avg_first
        if delta > 0.05:
            return "improving"
        elif delta < -0.05:
            return "degrading"
        return "stable"

    def get_dimension_breakdown(self, n: int = 10) -> Dict[str, str]:
        """Get per-dimension trend assessment."""
        recent = self._signals[-n:] if self._signals else []
        if len(recent) < 4:
            return {}

        mid = len(recent) // 2
        first_half = recent[:mid]
        second_half = recent[mid:]

        breakdown: Dict[str, str] = {}
        for dim in ("correctness", "efficiency", "stability", "elegance"):
            avg_first = sum(getattr(s, dim) for s in first_half) / len(first_half)
            avg_second = sum(getattr(s, dim) for s in second_half) / len(second_half)
            delta = avg_second - avg_first
            if delta > 0.05:
                breakdown[dim] = "improving"
            elif delta < -0.05:
                breakdown[dim] = "degrading"
            else:
                breakdown[dim] = "stable"
        return breakdown


__all__ = [
    "RewardAggregator",
    "RewardCalculator",
    "RewardSignal",
]
