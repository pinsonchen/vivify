"""Feature 状态机 — 声明式状态图 + 转移校验"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────────
# 合法状态转移图
# ────────────────────────────────────────────────────────────────────────────────

TRANSITIONS: dict[str, list[str]] = {
    "pending":    ["evaluating"],
    "evaluating": ["approved", "rejected"],
    "approved":   ["developing", "rejected"],
    "developing": ["deployed", "deployed_with_issues", "approved"],  # approved = rollback retry
    "deployed":   ["verifying", "deployed_with_issues"],
    "verifying":  ["verified", "deployed_with_issues"],
    "verified":   [],  # terminal
    "rejected":   [],  # terminal
    "deployed_with_issues": ["approved", "rejected"],  # recovery or give up
}

ALL_STATES: set[str] = set(TRANSITIONS.keys())
TERMINAL_STATES: set[str] = {s for s, targets in TRANSITIONS.items() if not targets}
ACTIVE_STATES: set[str] = ALL_STATES - TERMINAL_STATES


# ────────────────────────────────────────────────────────────────────────────────
# Exceptions
# ────────────────────────────────────────────────────────────────────────────────


class InvalidTransitionError(Exception):
    """非法状态转移"""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current} -> {target}")


# ────────────────────────────────────────────────────────────────────────────────
# State machine
# ────────────────────────────────────────────────────────────────────────────────


class FeatureStateMachine:
    """Feature 状态机，校验状态转移合法性"""

    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        """检查转移是否合法"""
        allowed = TRANSITIONS.get(current, [])
        return target in allowed

    @staticmethod
    def validate_transition(current: str, target: str) -> None:
        """校验转移，非法时抛出异常"""
        if not FeatureStateMachine.can_transition(current, target):
            raise InvalidTransitionError(current, target)

    @staticmethod
    def get_allowed_transitions(current: str) -> list[str]:
        """获取当前状态的合法目标状态列表"""
        return list(TRANSITIONS.get(current, []))

    @staticmethod
    def is_terminal(status: str) -> bool:
        """是否为终态"""
        return status in TERMINAL_STATES

    @staticmethod
    def is_active(status: str) -> bool:
        """是否为活跃状态（可继续处理）"""
        return status in ACTIVE_STATES


# ────────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────────────────────────────────────


def get_processable_statuses() -> list[str]:
    """返回 pipeline 可以处理的状态列表（非终态）"""
    return sorted(ACTIVE_STATES)


def get_status_phase(status: str) -> str:
    """返回状态所属阶段"""
    phases = {
        "pending": "queue",
        "evaluating": "evaluation",
        "approved": "ready",
        "developing": "development",
        "deployed": "deployment",
        "deployed_with_issues": "recovery",
        "verifying": "verification",
        "verified": "complete",
        "rejected": "complete",
    }
    return phases.get(status, "unknown")


__all__ = [
    "TRANSITIONS",
    "ALL_STATES",
    "TERMINAL_STATES",
    "ACTIVE_STATES",
    "InvalidTransitionError",
    "FeatureStateMachine",
    "get_processable_statuses",
    "get_status_phase",
]
