"""Coding agent interface.

A coding agent is anything that can take a natural-language prompt and produce
code changes inside a workspace directory. The default implementation wraps
``qodercli`` (see ``vivify.agents.qodercli_agent``), but the interface is
intentionally generic so other agents (Claude Code, Aider, Codex, ...) can be
added later without touching the kernel.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Mapping, Optional

from vivify.models import AgentResult


class CodingAgent(ABC):
    """Pluggable coding agent that turns prompts into committed code changes."""

    @abstractmethod
    def name(self) -> str:
        """Stable identifier (e.g. ``"qodercli"``) — used for logs and storage."""

    @abstractmethod
    def heal(
        self,
        prompt: str,
        *,
        max_turns: int,
        category: str,
        workspace: Path,
        env: Optional[Mapping[str, str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> AgentResult:
        """Run the agent against ``prompt`` inside ``workspace``.

        Implementations MUST:

        * Run inside ``workspace`` (typically a git worktree). Never mutate the
          parent repository directly.
        * Tag any spawned subprocesses with ``VIVIFY_AGENT=1`` in their
          environment so the slot manager can count them reliably.
        * Honour ``max_turns`` / ``timeout_seconds`` to bound cost.
        * Capture stdout / stderr into :pyattr:`AgentResult.output` for later
          parsing (commit hashes, ``next_steps`` JSON blocks, etc.).

        ``category`` is a short tag (``"fix_issue"``, ``"feature_develop"``,
        ``"goal_decompose"``, ``"self_grow"`` ...) used by the storage layer to
        bucket action logs and by the slot manager for prioritisation.
        """

    def healthcheck(self) -> tuple[bool, str]:
        """Quick ``--version`` style probe used by ``vivify doctor``.

        Default returns ``(True, "")``; override to verify the binary exists,
        the model is reachable, etc.
        """
        return True, ""
