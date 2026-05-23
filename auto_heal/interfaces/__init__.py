"""Abstract base classes for all pluggable components.

The kernel only ever talks to these ABCs; concrete implementations live elsewhere
(``probes/``, ``fixers/``, ``storage/``, ``agents/``, ``goals/``, ``reporter/``,
``verifier/``). This is what makes auto-heal project-agnostic.
"""
from auto_heal.interfaces.probe import Probe, ProbeContext
from auto_heal.interfaces.fixer import Fixer, FixContext
from auto_heal.interfaces.storage import StorageProvider
from auto_heal.interfaces.agent import CodingAgent
from auto_heal.interfaces.goal_decomposer import GoalDecomposer, RepoState
from auto_heal.interfaces.reporter import Reporter
from auto_heal.interfaces.verifier import Verifier, VerifyResult

__all__ = [
    "Probe", "ProbeContext",
    "Fixer", "FixContext",
    "StorageProvider",
    "CodingAgent",
    "GoalDecomposer", "RepoState",
    "Reporter",
    "Verifier", "VerifyResult",
]
