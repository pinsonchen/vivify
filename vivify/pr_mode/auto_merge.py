"""Auto-merge pull requests once GitHub checks pass.

Per plan §9 step 8 + §10:

* Plugin-only PRs may auto-merge when ``pr.auto_merge: true`` in config.
* Kernel / mixed PRs are *never* auto-merged regardless of config.
* We delegate to GitHub's native ``--auto`` flag so the merge happens
  server-side after required checks succeed; the kernel does not need to keep
  polling.
"""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from vivify.pr_mode.pr_creator import PullRequest
from vivify.pr_mode.self_grow_guard import GuardDecision

logger = logging.getLogger(__name__)


MergeMethod = str  # "squash" | "merge" | "rebase"


@dataclass
class AutoMergeConfig:
    enabled: bool = False
    method: MergeMethod = "squash"
    delete_branch: bool = True
    poll_timeout_seconds: int = 0  # 0 → fire-and-forget (rely on gh --auto)
    poll_interval_seconds: int = 30
    gh_timeout_seconds: int = 60


@dataclass
class MergeOutcome:
    requested: bool
    merged: bool
    skipped_reason: Optional[str] = None
    detail: str = ""


def _run(cmd: Sequence[str], *, cwd: Path | str | None = None,
         timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=timeout,
    )


class AutoMerge:
    """Request GitHub auto-merge (and optionally poll for the final state)."""

    def __init__(self, config: AutoMergeConfig | None = None):
        self.config = config or AutoMergeConfig()

    # ── public API ──────────────────────────────────────────────────────────
    def try_merge(
        self,
        pr: PullRequest,
        *,
        decision: GuardDecision | None = None,
        cwd: Path | str | None = None,
    ) -> MergeOutcome:
        """Enable ``gh pr merge --auto`` if config + guard allow it."""
        if not self.config.enabled:
            return MergeOutcome(False, False, skipped_reason="auto_merge disabled in config")
        if pr.draft:
            return MergeOutcome(False, False, skipped_reason="PR is draft")
        if decision is not None and not decision.allow_auto_merge:
            return MergeOutcome(
                False, False,
                skipped_reason=f"guard rejected ({decision.classification.value})",
            )
        if not pr.url and pr.number is None:
            return MergeOutcome(False, False, skipped_reason="missing PR identifier")

        target = str(pr.number) if pr.number is not None else pr.url
        cmd = ["gh", "pr", "merge", target, "--auto", f"--{self.config.method}"]
        if self.config.delete_branch:
            cmd.append("--delete-branch")

        result = _run(cmd, cwd=cwd, timeout=self.config.gh_timeout_seconds)
        if result.returncode != 0:
            logger.warning(
                "gh pr merge --auto failed for %s: %s",
                target, result.stderr.strip()[:300],
            )
            return MergeOutcome(
                requested=True, merged=False,
                detail=result.stderr.strip()[:300],
            )

        logger.info("Requested auto-merge for PR %s (method=%s)", target, self.config.method)

        if self.config.poll_timeout_seconds <= 0:
            return MergeOutcome(requested=True, merged=False,
                                detail="auto-merge enabled; not polling")
        return self._poll_until_merged(target, cwd=cwd)

    # ── internals ───────────────────────────────────────────────────────────
    def _poll_until_merged(self, target: str, *, cwd) -> MergeOutcome:
        deadline = time.time() + self.config.poll_timeout_seconds
        while time.time() < deadline:
            state = self._pr_state(target, cwd=cwd)
            if state == "MERGED":
                return MergeOutcome(True, True, detail="merged")
            if state == "CLOSED":
                return MergeOutcome(True, False, detail="PR closed without merge")
            time.sleep(max(5, self.config.poll_interval_seconds))
        return MergeOutcome(True, False, detail="poll timeout — auto-merge still pending")

    def _pr_state(self, target: str, *, cwd) -> str:
        res = _run(
            ["gh", "pr", "view", target, "--json", "state", "-q", ".state"],
            cwd=cwd, timeout=self.config.gh_timeout_seconds,
        )
        if res.returncode != 0:
            return ""
        return (res.stdout or "").strip().upper()

    def checks_passing(self, pr: PullRequest, *, cwd=None) -> bool:
        """Optional helper — returns True if ``gh pr checks`` reports all green."""
        target = str(pr.number) if pr.number is not None else pr.url
        if not target:
            return False
        res = _run(
            ["gh", "pr", "checks", target],
            cwd=cwd, timeout=self.config.gh_timeout_seconds,
        )
        # ``gh pr checks`` exits 0 only when every required check passed.
        return res.returncode == 0


__all__ = ["AutoMerge", "AutoMergeConfig", "MergeOutcome"]
