"""Reporter that mirrors high-severity ActionLog events as GitHub issues.

Drives the ``gh issue create`` command line — no API tokens are passed
explicitly, ``gh`` reads ``GH_TOKEN`` from the environment. Failures are
swallowed (issue mirroring must never break the kernel).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Iterable, Optional

from auto_heal.interfaces.reporter import Reporter
from auto_heal.models.snapshot import ActionLog

logger = logging.getLogger(__name__)


class GithubIssueReporter(Reporter):
    """Open a GitHub issue for high-severity actions (escalations, regressions)."""

    DEFAULT_LEVELS = ("CRITICAL", "HIGH")
    DEFAULT_ACTIONS = ("heal", "feature_develop", "feature_verify")

    def __init__(
        self,
        *,
        repo: Optional[str] = None,
        labels: Iterable[str] = ("auto-heal",),
        levels: Iterable[str] = DEFAULT_LEVELS,
        actions: Iterable[str] = DEFAULT_ACTIONS,
        only_failed: bool = True,
        gh_binary: str = "gh",
    ):
        self.repo = repo
        self.labels = tuple(labels)
        self.levels = tuple(levels)
        self.actions = tuple(actions)
        self.only_failed = only_failed
        self.gh_binary = gh_binary

    # ── public ─────────────────────────────────────────────────────────────
    def report(self, action: ActionLog) -> None:
        try:
            if not self._should_mirror(action):
                return
            if shutil.which(self.gh_binary) is None:
                logger.debug("gh CLI missing; skipping GitHub mirror")
                return
            cmd = [
                self.gh_binary, "issue", "create",
                "--title", self._title(action),
                "--body", self._body(action),
            ]
            for label in self.labels:
                cmd.extend(["--label", label])
            if self.repo:
                cmd.extend(["--repo", self.repo])
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if res.returncode != 0:
                logger.warning(
                    "GithubIssueReporter: gh failed (%s) — %s",
                    res.returncode, res.stderr.strip()[:200],
                )
        except Exception as e:  # pragma: no cover
            logger.debug("GithubIssueReporter swallowed: %s", e)

    # ── helpers ────────────────────────────────────────────────────────────
    def _should_mirror(self, action: ActionLog) -> bool:
        if action.action_type not in self.actions:
            return False
        if self.only_failed and action.status != "failed":
            return False
        if action.level and action.level.upper() not in self.levels:
            return False
        return True

    @staticmethod
    def _title(action: ActionLog) -> str:
        prefix = f"[auto-heal] {action.action_type}/{action.status}"
        title = action.title or action.category or "unknown"
        return f"{prefix}: {title}"[:200]

    @staticmethod
    def _body(action: ActionLog) -> str:
        rows = [
            f"- run: `{action.run_id}` round=`{action.round_num}`",
            f"- category: `{action.category or '-'}`",
            f"- level: `{action.level or '-'}`",
        ]
        if action.duration_seconds is not None:
            rows.append(f"- duration: `{action.duration_seconds:.1f}s`")
        if action.pr_url:
            rows.append(f"- PR: {action.pr_url}")
        if action.commit_hash:
            rows.append(f"- commit: `{action.commit_hash}`")
        body = "## auto-heal event\n\n" + "\n".join(rows)
        if action.result_summary:
            body += f"\n\n### Summary\n```\n{action.result_summary[:1500]}\n```"
        return body


__all__ = ["GithubIssueReporter"]
