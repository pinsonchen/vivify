"""Coding agents and prompt machinery.

Default agent: :class:`QoderCliAgent`. Pluggable via
``agent.type`` in ``.auto-heal.yml``.
"""
from auto_heal.agents.history import load_history
from auto_heal.agents.qodercli_agent import QoderCliAgent, QoderCliConfig
from auto_heal.agents.slot_manager import (
    AGENT_ENV_TAG,
    count_agent_processes,
    wait_for_slot,
)

__all__ = [
    "QoderCliAgent",
    "QoderCliConfig",
    "load_history",
    "AGENT_ENV_TAG",
    "count_agent_processes",
    "wait_for_slot",
]
