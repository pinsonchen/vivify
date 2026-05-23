"""Abstract base classes for all pluggable components.

The kernel only ever talks to these ABCs; concrete implementations live elsewhere
(``probes/``, ``fixers/``, ``storage/``, ``agents/``, ``goals/``, ``reporter/``,
``verifier/``). This is what makes vivify project-agnostic.
"""
from vivify.interfaces.probe import Probe, ProbeContext
from vivify.interfaces.fixer import Fixer, FixContext
from vivify.interfaces.storage import StorageProvider
from vivify.interfaces.agent import CodingAgent
from vivify.interfaces.goal_decomposer import GoalDecomposer, RepoState
from vivify.interfaces.reporter import Reporter
from vivify.interfaces.verifier import Verifier, VerifyResult

__all__ = [
    "Probe", "ProbeContext",
    "Fixer", "FixContext",
    "StorageProvider",
    "CodingAgent",
    "GoalDecomposer", "RepoState",
    "Reporter",
    "Verifier", "VerifyResult",
]
