"""Unit tests for vivify.kernel.token_budget — TokenBucket + P53Suppressor."""
from __future__ import annotations

import time

import pytest

from vivify.kernel.token_budget import BudgetConfig, P53Suppressor, TokenBucket


# ──────────────────────────────────────────────────────────────────────────────
# BudgetConfig defaults
# ──────────────────────────────────────────────────────────────────────────────


class TestBudgetConfig:
    def test_defaults(self):
        cfg = BudgetConfig()
        assert cfg.daily_limit == 100
        assert cfg.per_cycle_limit == 10
        assert cfg.window_seconds == 86400
        assert cfg.pr_frequency_threshold == 10
        assert cfg.backlog_threshold == 20
        assert cfg.cooldown_multiplier == 2.0

    def test_custom_values(self):
        cfg = BudgetConfig(daily_limit=50, per_cycle_limit=5)
        assert cfg.daily_limit == 50
        assert cfg.per_cycle_limit == 5


# ──────────────────────────────────────────────────────────────────────────────
# TokenBucket
# ──────────────────────────────────────────────────────────────────────────────


class TestTokenBucket:
    def _bucket(self, **kwargs) -> TokenBucket:
        cfg = BudgetConfig(**kwargs)
        return TokenBucket(config=cfg)

    def test_basic_consume(self):
        bucket = self._bucket(daily_limit=5, per_cycle_limit=3)
        assert bucket.can_consume()
        assert bucket.consume()
        assert bucket.remaining_daily == 4
        assert bucket.remaining_cycle == 2

    def test_daily_limit_reached(self):
        bucket = self._bucket(daily_limit=3, per_cycle_limit=10)
        for _ in range(3):
            assert bucket.consume()
        assert not bucket.can_consume()
        assert not bucket.consume()
        assert bucket.remaining_daily == 0

    def test_per_cycle_limit_reached(self):
        bucket = self._bucket(daily_limit=100, per_cycle_limit=2)
        assert bucket.consume()
        assert bucket.consume()
        assert not bucket.can_consume()
        assert not bucket.consume()
        assert bucket.remaining_cycle == 0

    def test_reset_cycle(self):
        bucket = self._bucket(daily_limit=100, per_cycle_limit=2)
        bucket.consume()
        bucket.consume()
        assert not bucket.can_consume()
        bucket.reset_cycle()
        assert bucket.can_consume()
        assert bucket.remaining_cycle == 2
        # daily_used should not reset
        assert bucket.remaining_daily == 98

    def test_window_reset(self):
        bucket = self._bucket(daily_limit=3, per_cycle_limit=10, window_seconds=1)
        bucket.consume()
        bucket.consume()
        bucket.consume()
        assert not bucket.can_consume()
        # Simulate window passage
        bucket._window_start = time.time() - 2
        assert bucket.can_consume()
        assert bucket.remaining_daily == 3

    def test_suppress_blocks_consume(self):
        bucket = self._bucket()
        bucket.suppress("test reason")
        assert bucket.is_suppressed
        assert bucket.suppression_reason == "test reason"
        assert not bucket.can_consume()
        assert not bucket.consume()

    def test_unsuppress(self):
        bucket = self._bucket()
        bucket.suppress("test")
        assert bucket.is_suppressed
        bucket.unsuppress()
        assert not bucket.is_suppressed
        assert bucket.suppression_reason == ""
        assert bucket.can_consume()

    def test_consume_multiple_tokens(self):
        bucket = self._bucket(daily_limit=5, per_cycle_limit=3)
        assert bucket.can_consume(2)
        assert bucket.consume(2)
        assert bucket.remaining_daily == 3
        assert bucket.remaining_cycle == 1
        # Cannot consume 2 more in this cycle (only 1 left)
        assert not bucket.can_consume(2)
        assert not bucket.consume(2)

    def test_usage_report_format(self):
        bucket = self._bucket(daily_limit=10, per_cycle_limit=5)
        bucket.consume(3)
        report = bucket.usage_report
        assert report == {
            "daily_used": 3,
            "daily_limit": 10,
            "daily_remaining": 7,
            "cycle_used": 3,
            "cycle_limit": 5,
            "suppressed": False,
            "suppression_reason": "",
        }

    def test_usage_report_suppressed(self):
        bucket = self._bucket()
        bucket.suppress("overload")
        report = bucket.usage_report
        assert report["suppressed"] is True
        assert report["suppression_reason"] == "overload"


# ──────────────────────────────────────────────────────────────────────────────
# P53Suppressor
# ──────────────────────────────────────────────────────────────────────────────


class TestP53Suppressor:
    def _suppressor(self, **kwargs) -> P53Suppressor:
        cfg = BudgetConfig(**kwargs)
        return P53Suppressor(config=cfg)

    def test_no_suppression_normal(self):
        s = self._suppressor()
        result = s.evaluate({
            "pr_count_24h": 5,
            "pending_features": 10,
            "failed_fixes_24h": 3,
        })
        assert result is None

    def test_suppression_pr_frequency(self):
        s = self._suppressor(pr_frequency_threshold=5)
        result = s.evaluate({"pr_count_24h": 8, "pending_features": 0, "failed_fixes_24h": 0})
        assert result is not None
        assert "PR frequency too high" in result
        assert "8" in result

    def test_suppression_backlog(self):
        s = self._suppressor(backlog_threshold=10)
        result = s.evaluate({"pr_count_24h": 0, "pending_features": 15, "failed_fixes_24h": 0})
        assert result is not None
        assert "backlog too deep" in result

    def test_suppression_failure_rate(self):
        s = self._suppressor(pr_frequency_threshold=3)
        result = s.evaluate({"pr_count_24h": 0, "pending_features": 0, "failed_fixes_24h": 5})
        assert result is not None
        assert "failure rate too high" in result

    def test_suppression_multiple_reasons(self):
        s = self._suppressor(pr_frequency_threshold=2, backlog_threshold=5)
        result = s.evaluate({"pr_count_24h": 5, "pending_features": 10, "failed_fixes_24h": 0})
        assert result is not None
        assert "PR frequency" in result
        assert "backlog" in result
        assert ";" in result  # multiple reasons joined

    def test_boundary_exact_threshold_no_suppression(self):
        """Exactly at threshold should NOT trigger (> not >=)."""
        s = self._suppressor(pr_frequency_threshold=10, backlog_threshold=20)
        result = s.evaluate({"pr_count_24h": 10, "pending_features": 20, "failed_fixes_24h": 10})
        assert result is None

    def test_missing_metrics_keys(self):
        """Missing keys default to 0 — no suppression."""
        s = self._suppressor()
        result = s.evaluate({})
        assert result is None

    def test_threshold_just_above(self):
        s = self._suppressor(pr_frequency_threshold=10)
        result = s.evaluate({"pr_count_24h": 11, "pending_features": 0, "failed_fixes_24h": 0})
        assert result is not None
        assert "11" in result
