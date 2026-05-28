"""Data models for the harness sub-module (PEV verification loop)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SensorResult:
    """Single sensor execution result."""

    sensor_type: str        # "test" | "lint" | "typecheck" | "build"
    passed: bool
    output: str             # truncated to 2000 chars
    duration_seconds: float
    exit_code: int


@dataclass
class HarnessReport:
    """Complete harness verification report."""

    sensors: list[SensorResult] = field(default_factory=list)
    all_passed: bool = True
    risk_level: str = "low"         # "low" | "medium" | "high"
    feedback_prompt: str = ""       # generated when sensors fail
    doom_loop_detected: bool = False


@dataclass
class RiskAssessment:
    """Risk assessment for a code change."""

    score: int = 0              # 0-100
    level: str = "low"          # "low" | "medium" | "high"
    factors: list[str] = field(default_factory=list)


__all__ = ["SensorResult", "HarnessReport", "RiskAssessment"]
