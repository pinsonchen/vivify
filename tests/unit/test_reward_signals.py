"""Unit tests for vivify.intelligence.reward_signals."""
from __future__ import annotations

import pytest

from vivify.intelligence.reward_signals import (
    RewardAggregator,
    RewardCalculator,
    RewardSignal,
)


# ────────────────────────────────────────────────────────────────────────────────
# RewardSignal
# ────────────────────────────────────────────────────────────────────────────────


class TestRewardSignal:
    """Tests for the RewardSignal dataclass."""

    def test_default_composite_score(self):
        """Default signal (all 0.5) gives composite = 0.5."""
        signal = RewardSignal(action_id="test-1")
        assert signal.composite_score == pytest.approx(0.5, abs=0.001)

    def test_composite_with_weights(self):
        """Composite uses 40/20/25/15 weighting."""
        signal = RewardSignal(action_id="test-2")
        signal.correctness = 1.0
        signal.efficiency = 1.0
        signal.stability = 1.0
        signal.elegance = 1.0
        assert signal.composite_score == pytest.approx(1.0, abs=0.001)

    def test_composite_custom_weights(self):
        """Custom weights alter composite score."""
        signal = RewardSignal(
            action_id="test-3",
            correctness=1.0,
            efficiency=0.0,
            stability=0.0,
            elegance=0.0,
            _weights={"correctness": 1.0, "efficiency": 0.0, "stability": 0.0, "elegance": 0.0},
        )
        assert signal.composite_score == pytest.approx(1.0, abs=0.001)

    def test_is_positive_threshold(self):
        """is_positive True when score >= 0.6."""
        signal = RewardSignal(action_id="pos")
        signal.correctness = 0.8
        signal.efficiency = 0.7
        signal.stability = 0.6
        signal.elegance = 0.5
        assert signal.is_positive is True

        # All at 0.3 should be negative
        low = RewardSignal(action_id="neg")
        low.correctness = 0.3
        low.efficiency = 0.3
        low.stability = 0.3
        low.elegance = 0.3
        assert low.is_positive is False

    def test_category_excellent(self):
        signal = RewardSignal(action_id="a")
        signal.correctness = 1.0
        signal.efficiency = 0.9
        signal.stability = 0.9
        signal.elegance = 0.8
        assert signal.category == "excellent"

    def test_category_good(self):
        signal = RewardSignal(action_id="b")
        signal.correctness = 0.7
        signal.efficiency = 0.6
        signal.stability = 0.6
        signal.elegance = 0.6
        assert signal.category == "good"

    def test_category_neutral(self):
        signal = RewardSignal(action_id="c")
        signal.correctness = 0.5
        signal.efficiency = 0.5
        signal.stability = 0.5
        signal.elegance = 0.5
        assert signal.category == "neutral"

    def test_category_poor(self):
        signal = RewardSignal(action_id="d")
        signal.correctness = 0.1
        signal.efficiency = 0.2
        signal.stability = 0.1
        signal.elegance = 0.2
        assert signal.category == "poor"

    def test_to_dict(self):
        signal = RewardSignal(action_id="dict-test")
        d = signal.to_dict()
        assert d["action_id"] == "dict-test"
        assert "composite_score" in d
        assert "category" in d
        assert "is_positive" in d


# ────────────────────────────────────────────────────────────────────────────────
# RewardCalculator
# ────────────────────────────────────────────────────────────────────────────────


class TestRewardCalculator:
    """Tests for RewardCalculator dimension computation."""

    def setup_method(self):
        self.calc = RewardCalculator()

    def test_calculate_immediate_success(self):
        """Successful action with improvements scores well."""
        result = {
            "action_id": "fix-001",
            "success": True,
            "tests_before": 8,
            "tests_after": 10,
            "tests_total": 10,
            "lint_errors_before": 5,
            "lint_errors_after": 2,
            "type_errors_before": 3,
            "type_errors_after": 0,
            "agent_turns": 5,
            "diff_lines_added": 10,
            "diff_lines_removed": 20,
            "files_changed": 2,
            "new_dependencies": 0,
            "lint_warnings_delta": -3,
        }
        signal = self.calc.calculate_immediate(result)
        assert signal.action_id == "fix-001"
        assert signal.correctness > 0.7
        assert signal.efficiency > 0.7
        assert signal.elegance > 0.6
        assert signal.stability == 0.5  # Neutral initially

    def test_calculate_immediate_failure(self):
        """Failed action gets low correctness."""
        result = {
            "action_id": "fix-002",
            "success": False,
        }
        signal = self.calc.calculate_immediate(result)
        assert signal.correctness == pytest.approx(0.1, abs=0.001)

    def test_correctness_test_regression(self):
        """Test regression lowers correctness."""
        result = {
            "action_id": "fix-003",
            "success": True,
            "tests_before": 10,
            "tests_after": 7,
            "tests_total": 10,
            "lint_errors_before": 0,
            "lint_errors_after": 0,
            "type_errors_before": 0,
            "type_errors_after": 0,
        }
        signal = self.calc.calculate_immediate(result)
        # Should be lower due to test regression (penalized but lint/type offset)
        assert signal.correctness <= 0.5

    def test_correctness_lint_regression(self):
        """Lint errors increase lowers correctness."""
        result = {
            "action_id": "fix-004",
            "success": True,
            "tests_before": 5,
            "tests_after": 5,
            "tests_total": 5,
            "lint_errors_before": 0,
            "lint_errors_after": 5,
            "type_errors_before": 0,
            "type_errors_after": 0,
        }
        signal = self.calc.calculate_immediate(result)
        assert signal.correctness < 0.8

    def test_efficiency_few_turns(self):
        """Few agent turns reward efficiency."""
        result = {
            "action_id": "eff-1",
            "success": True,
            "agent_turns": 3,
            "diff_lines_added": 5,
            "diff_lines_removed": 10,
        }
        signal = self.calc.calculate_immediate(result)
        assert signal.efficiency >= 0.8

    def test_efficiency_many_turns(self):
        """Many agent turns penalize efficiency."""
        result = {
            "action_id": "eff-2",
            "success": True,
            "agent_turns": 60,
            "diff_lines_added": 100,
            "diff_lines_removed": 5,
        }
        signal = self.calc.calculate_immediate(result)
        assert signal.efficiency < 0.5

    def test_elegance_reduced_warnings(self):
        """Reduced lint warnings improve elegance."""
        result = {
            "action_id": "elg-1",
            "success": True,
            "lint_warnings_delta": -5,
            "files_changed": 2,
            "new_dependencies": 0,
        }
        signal = self.calc.calculate_immediate(result)
        assert signal.elegance >= 0.7

    def test_elegance_new_dependencies_penalty(self):
        """New dependencies reduce elegance."""
        result = {
            "action_id": "elg-2",
            "success": True,
            "lint_warnings_delta": 0,
            "files_changed": 2,
            "new_dependencies": 3,
        }
        signal = self.calc.calculate_immediate(result)
        assert signal.elegance < 0.5

    def test_elegance_many_files_penalty(self):
        """Changing many files lowers elegance."""
        result = {
            "action_id": "elg-3",
            "success": True,
            "lint_warnings_delta": 0,
            "files_changed": 15,
            "new_dependencies": 0,
        }
        signal = self.calc.calculate_immediate(result)
        assert signal.elegance < 0.6

    # ── Stability retroactive update ────────────────────────────────────

    def test_stability_no_regression(self):
        """Stable fix after 5 days scores high."""
        signal = RewardSignal(action_id="stab-1")
        self.calc.update_stability(signal, {
            "regressed": False,
            "days_since_fix": 5,
            "pr_reverted": False,
            "same_probe_hit_count": 0,
        })
        assert signal.stability >= 0.8

    def test_stability_pr_reverted(self):
        """Reverted PR gets zero stability."""
        signal = RewardSignal(action_id="stab-2")
        self.calc.update_stability(signal, {
            "regressed": False,
            "days_since_fix": 1,
            "pr_reverted": True,
            "same_probe_hit_count": 0,
        })
        assert signal.stability == 0.0

    def test_stability_regressed(self):
        """Regression gives low stability."""
        signal = RewardSignal(action_id="stab-3")
        self.calc.update_stability(signal, {
            "regressed": True,
            "days_since_fix": 2,
            "pr_reverted": False,
            "same_probe_hit_count": 1,
        })
        assert signal.stability == 0.2

    def test_stability_probe_hits(self):
        """Same probe hitting again degrades stability."""
        signal = RewardSignal(action_id="stab-4")
        self.calc.update_stability(signal, {
            "regressed": False,
            "days_since_fix": 3,
            "pr_reverted": False,
            "same_probe_hit_count": 3,
        })
        assert signal.stability == pytest.approx(0.5, abs=0.001)

    def test_stability_grows_with_time(self):
        """Stability score increases with more days stable."""
        signal_1 = RewardSignal(action_id="grow-1")
        self.calc.update_stability(signal_1, {
            "regressed": False,
            "days_since_fix": 1,
            "pr_reverted": False,
            "same_probe_hit_count": 0,
        })
        signal_7 = RewardSignal(action_id="grow-7")
        self.calc.update_stability(signal_7, {
            "regressed": False,
            "days_since_fix": 7,
            "pr_reverted": False,
            "same_probe_hit_count": 0,
        })
        assert signal_7.stability > signal_1.stability


# ────────────────────────────────────────────────────────────────────────────────
# RewardAggregator
# ────────────────────────────────────────────────────────────────────────────────


class TestRewardAggregator:
    """Tests for the aggregator layer."""

    def setup_method(self):
        self.agg = RewardAggregator()

    def test_record_and_get_average(self):
        """Recording actions populates averages."""
        for i in range(5):
            self.agg.record_action_reward({
                "action_id": f"a-{i}",
                "success": True,
                "agent_turns": 10,
                "diff_lines_added": 5,
                "diff_lines_removed": 5,
                "files_changed": 2,
                "new_dependencies": 0,
                "lint_warnings_delta": 0,
            })
        avg = self.agg.get_recent_average(n=5)
        assert "composite" in avg
        assert avg["composite"] > 0.4

    def test_get_average_empty(self):
        """Empty aggregator returns neutral averages."""
        avg = self.agg.get_recent_average()
        assert avg["composite"] == 0.5
        assert avg["correctness"] == 0.5

    def test_trend_insufficient_data(self):
        """Fewer signals than window returns insufficient_data."""
        self.agg.record_action_reward({
            "action_id": "single",
            "success": True,
            "agent_turns": 10,
        })
        assert self.agg.get_trend(window=20) == "insufficient_data"

    def test_trend_improving(self):
        """Improving trend when later signals are better."""
        # First half: poor signals
        for i in range(10):
            self.agg.record_action_reward({
                "action_id": f"poor-{i}",
                "success": False,
            })
        # Second half: good signals
        for i in range(10):
            self.agg.record_action_reward({
                "action_id": f"good-{i}",
                "success": True,
                "tests_before": 8,
                "tests_after": 10,
                "tests_total": 10,
                "lint_errors_before": 5,
                "lint_errors_after": 0,
                "type_errors_before": 3,
                "type_errors_after": 0,
                "agent_turns": 3,
                "diff_lines_added": 5,
                "diff_lines_removed": 15,
                "files_changed": 1,
                "new_dependencies": 0,
                "lint_warnings_delta": -5,
            })
        assert self.agg.get_trend(window=20) == "improving"

    def test_trend_degrading(self):
        """Degrading trend when later signals are worse."""
        # First half: excellent
        for i in range(10):
            self.agg.record_action_reward({
                "action_id": f"exc-{i}",
                "success": True,
                "tests_before": 9,
                "tests_after": 10,
                "tests_total": 10,
                "lint_errors_before": 5,
                "lint_errors_after": 0,
                "type_errors_before": 2,
                "type_errors_after": 0,
                "agent_turns": 3,
                "diff_lines_added": 2,
                "diff_lines_removed": 10,
                "files_changed": 1,
                "new_dependencies": 0,
                "lint_warnings_delta": -3,
            })
        # Second half: poor
        for i in range(10):
            self.agg.record_action_reward({
                "action_id": f"bad-{i}",
                "success": False,
            })
        assert self.agg.get_trend(window=20) == "degrading"

    def test_max_history_trimming(self):
        """Aggregator trims history to max_history."""
        agg = RewardAggregator(max_history=5)
        for i in range(10):
            agg.record_action_reward({
                "action_id": f"h-{i}",
                "success": True,
                "agent_turns": 10,
            })
        assert len(agg.signals) == 5

    def test_dimension_breakdown_empty(self):
        """Empty aggregator returns empty breakdown."""
        assert self.agg.get_dimension_breakdown() == {}

    def test_dimension_breakdown_with_data(self):
        """Dimension breakdown returns per-dimension trends."""
        for i in range(10):
            self.agg.record_action_reward({
                "action_id": f"dim-{i}",
                "success": True,
                "agent_turns": 10,
                "files_changed": 2,
                "new_dependencies": 0,
                "lint_warnings_delta": 0,
            })
        breakdown = self.agg.get_dimension_breakdown(n=10)
        assert "correctness" in breakdown
        assert "efficiency" in breakdown
        assert "stability" in breakdown
        assert "elegance" in breakdown


# ────────────────────────────────────────────────────────────────────────────────
# Edge cases
# ────────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions and edge cases."""

    def test_all_zeros(self):
        """Action result with all zeros doesn't crash."""
        calc = RewardCalculator()
        result = {
            "action_id": "zero",
            "success": True,
            "tests_before": 0,
            "tests_after": 0,
            "tests_total": 0,
            "lint_errors_before": 0,
            "lint_errors_after": 0,
            "type_errors_before": 0,
            "type_errors_after": 0,
            "build_time_before": 0,
            "build_time_after": 0,
            "agent_turns": 0,
            "diff_lines_added": 0,
            "diff_lines_removed": 0,
            "files_changed": 0,
            "new_dependencies": 0,
            "lint_warnings_delta": 0,
        }
        signal = calc.calculate_immediate(result)
        assert 0.0 <= signal.composite_score <= 1.0

    def test_all_max_values(self):
        """Extreme values are clamped properly."""
        calc = RewardCalculator()
        result = {
            "action_id": "max",
            "success": True,
            "tests_before": 100,
            "tests_after": 100,
            "tests_total": 100,
            "lint_errors_before": 999,
            "lint_errors_after": 0,
            "type_errors_before": 999,
            "type_errors_after": 0,
            "build_time_before": 60.0,
            "build_time_after": 10.0,
            "agent_turns": 1,
            "diff_lines_added": 1,
            "diff_lines_removed": 100,
            "files_changed": 1,
            "new_dependencies": 0,
            "lint_warnings_delta": -50,
        }
        signal = calc.calculate_immediate(result)
        assert signal.correctness <= 1.0
        assert signal.efficiency <= 1.0
        assert signal.elegance <= 1.0

    def test_empty_action_result(self):
        """Minimal action_result doesn't crash."""
        calc = RewardCalculator()
        signal = calc.calculate_immediate({})
        # No success key → defaults to False → correctness 0.1
        assert signal.correctness == pytest.approx(0.1, abs=0.001)

    def test_custom_weights_sum_doesnt_need_to_be_one(self):
        """Custom weights work even if they don't sum to 1.0."""
        calc = RewardCalculator(weights={
            "correctness": 0.5,
            "efficiency": 0.5,
            "stability": 0.5,
            "elegance": 0.5,
        })
        result = {"action_id": "w", "success": True, "agent_turns": 5}
        signal = calc.calculate_immediate(result)
        # All dims should be 0.5+ ; composite > 0.5
        assert signal.composite_score > 0.5

    def test_stability_clamped_to_1(self):
        """Stability can't exceed 1.0 even with many days."""
        calc = RewardCalculator()
        signal = RewardSignal(action_id="s")
        calc.update_stability(signal, {
            "regressed": False,
            "days_since_fix": 100,
            "pr_reverted": False,
            "same_probe_hit_count": 0,
        })
        assert signal.stability == 1.0

    def test_stability_probe_hits_floor(self):
        """Stability with many probe hits doesn't go below 0.3."""
        calc = RewardCalculator()
        signal = RewardSignal(action_id="floor")
        calc.update_stability(signal, {
            "regressed": False,
            "days_since_fix": 1,
            "pr_reverted": False,
            "same_probe_hit_count": 100,
        })
        assert signal.stability == pytest.approx(0.3, abs=0.001)
