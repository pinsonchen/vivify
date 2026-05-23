"""Probe ABC + ProbeContext.

A probe is *the* unit of detection. It collects raw signals from the repo / CI / external
APIs in ``collect()`` and turns them into ``Issue`` instances in ``analyze()``.

Two concrete probe styles exist: ``YamlProbe`` (declarative, in ``probes/base.py``) and
direct subclasses (Python plugins users drop in ``.vivify/probes/``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, Tuple

from vivify.models.issue import Issue

if TYPE_CHECKING:
    from vivify.config.schema import VivifyConfig
    from vivify.interfaces.storage import StorageProvider


@dataclass
class ProbeContext:
    """Runtime context handed to every probe. NEVER read globals — read this."""

    repo_root: Path
    config: "VivifyConfig"
    storage: "StorageProvider"
    logger: Logger
    probe_config: dict = field(default_factory=dict)  # ``probes.overrides[<id>]`` merged with defaults
    extra: dict = field(default_factory=dict)         # for advanced wiring (gh facade, git facade, etc.)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class Probe(ABC):
    """Subclass + set ``id`` (and optionally ``description``, ``enabled_by_default``)."""

    id: str = ""
    description: str = ""
    enabled_by_default: bool = True
    runs_on: tuple[str, ...] = ()  # optional language/runtime hints (e.g. "python", "javascript")

    @abstractmethod
    def collect(self, ctx: ProbeContext) -> dict:
        """Return raw signal dict (used by ``analyze`` and ``Verifier`` for before/after diffs)."""

    @abstractmethod
    def analyze(self, raw: dict, ctx: ProbeContext) -> list[Issue]:
        """Convert raw signals into a list of standardized Issues."""

    def healthcheck(self, ctx: ProbeContext) -> Tuple[bool, str]:
        """Return (ok, hint). Defaults to OK; override to gate on tool availability."""
        return True, ""
