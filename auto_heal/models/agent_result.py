"""Result type for any ``CodingAgent.heal()`` invocation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentResult:
    success: bool
    output: str
    exit_code: int
    error: str | None = None
    duration_seconds: float = 0.0
