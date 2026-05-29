"""Token budget management - API call rate limiting and p53 tumor suppression.

Implements a two-layer defense:
1. **Hard limit** (TokenBucket): absolute daily and per-cycle caps that cannot be
   exceeded. When the budget is exhausted the kernel simply stops calling agents.
2. **Smart slowdown** (P53Suppressor): detects over-proliferation signals (too many
   PRs, deep backlogs, high failure rates) and proactively suppresses activity
   before the hard limit is even reached.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BudgetConfig:
    """Token budget configuration (mirrors pydantic schema for runtime use)."""

    daily_limit: int = 100          # 每日 API 调用硬限
    per_cycle_limit: int = 10       # 每轮循环最大调用数
    window_seconds: int = 86400     # 时间窗口（默认 24h）
    # p53 阈值
    pr_frequency_threshold: int = 10     # 24h 内 PR 数超过此值 → 降频
    backlog_threshold: int = 20          # 待处理 FR 积压超过此值 → 降频
    cooldown_multiplier: float = 2.0     # 降频时循环间隔倍增系数


@dataclass
class TokenBucket:
    """Global token bucket with hard limits.

    Tracks daily and per-cycle consumption. The bucket automatically resets when
    the configured time window elapses.
    """

    config: BudgetConfig
    _tokens_used: int = 0
    _cycle_tokens: int = 0
    _window_start: float = field(default_factory=time.time)
    _suppressed: bool = False
    _suppression_reason: str = ""

    def can_consume(self, tokens: int = 1) -> bool:
        """Check if tokens can be consumed without exceeding limits."""
        self._maybe_reset_window()
        if self._suppressed:
            return False
        if self._tokens_used + tokens > self.config.daily_limit:
            return False
        if self._cycle_tokens + tokens > self.config.per_cycle_limit:
            return False
        return True

    def consume(self, tokens: int = 1) -> bool:
        """Consume tokens. Returns False if budget exceeded."""
        if not self.can_consume(tokens):
            return False
        self._tokens_used += tokens
        self._cycle_tokens += tokens
        return True

    def reset_cycle(self):
        """Reset per-cycle counter (called at start of each loop iteration)."""
        self._cycle_tokens = 0

    def suppress(self, reason: str):
        """Activate p53 suppression — stop all activity."""
        self._suppressed = True
        self._suppression_reason = reason

    def unsuppress(self):
        """Manually lift suppression (human-only action)."""
        self._suppressed = False
        self._suppression_reason = ""

    @property
    def remaining_daily(self) -> int:
        self._maybe_reset_window()
        return max(0, self.config.daily_limit - self._tokens_used)

    @property
    def remaining_cycle(self) -> int:
        return max(0, self.config.per_cycle_limit - self._cycle_tokens)

    @property
    def is_suppressed(self) -> bool:
        return self._suppressed

    @property
    def suppression_reason(self) -> str:
        return self._suppression_reason

    @property
    def usage_report(self) -> dict:
        """Return a snapshot of current budget usage."""
        return {
            "daily_used": self._tokens_used,
            "daily_limit": self.config.daily_limit,
            "daily_remaining": self.remaining_daily,
            "cycle_used": self._cycle_tokens,
            "cycle_limit": self.config.per_cycle_limit,
            "suppressed": self._suppressed,
            "suppression_reason": self._suppression_reason,
        }

    def _maybe_reset_window(self):
        """Reset daily counter if the time window has elapsed."""
        now = time.time()
        if now - self._window_start >= self.config.window_seconds:
            self._tokens_used = 0
            self._window_start = now


@dataclass
class P53Suppressor:
    """Tumor suppression mechanism — detects over-proliferation and triggers slowdown.

    Inspired by the biological p53 protein that halts cell division when DNA
    damage accumulates. Here we halt vivify activity when operational signals
    suggest the system is spinning out of control.
    """

    config: BudgetConfig

    def evaluate(self, metrics: dict) -> Optional[str]:
        """Evaluate proliferation signals. Returns suppression reason or None.

        Expected *metrics* keys:
            - pr_count_24h: int  — PRs created in last 24 hours
            - pending_features: int — number of pending/in-progress features
            - failed_fixes_24h: int — number of failed fixes in last 24 hours
        """
        reasons: list[str] = []

        pr_count = metrics.get("pr_count_24h", 0)
        if pr_count > self.config.pr_frequency_threshold:
            reasons.append(
                f"PR frequency too high ({pr_count} in 24h, "
                f"threshold={self.config.pr_frequency_threshold})"
            )

        pending = metrics.get("pending_features", 0)
        if pending > self.config.backlog_threshold:
            reasons.append(
                f"Feature backlog too deep ({pending}, "
                f"threshold={self.config.backlog_threshold})"
            )

        # High failure rate indicates low value of continued activity
        failed = metrics.get("failed_fixes_24h", 0)
        if failed > self.config.pr_frequency_threshold:
            reasons.append(f"Fix failure rate too high ({failed} in 24h)")

        if reasons:
            return "; ".join(reasons)
        return None


__all__ = ["BudgetConfig", "P53Suppressor", "TokenBucket"]
