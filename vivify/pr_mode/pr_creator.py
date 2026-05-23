"""Push the worktree branch to ``origin`` and open a pull request via ``gh``.

This is the *only* place vivify turns local changes into something visible
on GitHub. The kernel never pushes to ``main`` directly — every change must
flow through this module.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from vivify.pr_mode.self_grow_guard import GuardDecision
from vivify.pr_mode.worktree import Worktree

logger = logging.getLogger(__name__)


@dataclass
class PullRequest:
    number: Optional[int]
    url: str
    branch: str
    base: str
    draft: bool
    labels: tuple[str, ...]


@dataclass
class PrCreatorConfig:
    base_branch: str = "main"
    default_labels: tuple[str, ...] = ("vivify",)
    default_draft: bool = False
    remote: str = "origin"
    push_timeout_seconds: int = 120
    gh_timeout_seconds: int = 60


def _run(cmd: Sequence[str], *, cwd: Path | str, timeout: int = 60,
         env: Optional[dict] = None) -> subprocess.CompletedProcess:
    # Explicitly inherit the current process environment so that secrets such
    # as ``GH_TOKEN`` (injected by the daemon manager into ``os.environ`` of
    # this process) reliably reach ``git`` / ``gh`` subprocesses, regardless
    # of how this process itself was started.
    if env is None:
        env = os.environ.copy()
    return subprocess.run(
        list(cmd), cwd=str(cwd), capture_output=True, text=True,
        timeout=timeout, env=env,
    )


_PR_URL_RE = re.compile(r"https?://[^\s]+/pull/(\d+)")


def _looks_like_missing_label(stderr: str | None) -> bool:
    """Heuristic: gh prints messages like ``could not add label: 'vivify' not found``.

    We match loosely on "label" + "not found" (case-insensitive) so the retry
    fires for the common ``gh`` phrasings without being overly aggressive.
    """
    if not stderr:
        return False
    s = stderr.lower()
    return "label" in s and "not found" in s


def _parse_pr_url(text: str) -> tuple[Optional[int], str]:
    """Pull the first ``…/pull/<n>`` URL out of ``gh`` output."""
    if not text:
        return None, ""
    m = _PR_URL_RE.search(text)
    if not m:
        return None, text.strip().splitlines()[-1] if text.strip() else ""
    return int(m.group(1)), m.group(0)


class PrCreator:
    """Push a worktree branch and open a PR."""

    def __init__(self, config: PrCreatorConfig | None = None):
        self.config = config or PrCreatorConfig()

    # ── public API ──────────────────────────────────────────────────────────
    def push_branch(self, worktree: Worktree) -> None:
        """``git push -u origin <branch>`` from inside the worktree."""
        result = _run(
            ["git", "push", "-u", self.config.remote, worktree.branch],
            cwd=worktree.path, timeout=self.config.push_timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git push failed for {worktree.branch}: "
                f"{result.stderr.strip()[:300]}"
            )
        logger.info("Pushed branch %s to %s", worktree.branch, self.config.remote)

    def open_pr(
        self,
        worktree: Worktree,
        *,
        title: str,
        body: str,
        decision: GuardDecision | None = None,
        extra_labels: Sequence[str] = (),
        draft: Optional[bool] = None,
        base: Optional[str] = None,
    ) -> PullRequest:
        """Run ``gh pr create`` and return the resulting :class:`PullRequest`."""
        labels: list[str] = list(self.config.default_labels)
        if decision is not None:
            labels.extend(decision.labels)
        labels.extend(extra_labels)
        # de-dupe while preserving order
        seen: set[str] = set()
        labels = [lbl for lbl in labels if not (lbl in seen or seen.add(lbl))]

        force_draft = bool(decision and decision.force_draft)
        is_draft = bool(force_draft if force_draft else (draft if draft is not None
                        else self.config.default_draft))

        base_branch = base or self.config.base_branch

        # Persist body to a temp file so we can use ``--body-file`` and avoid
        # any quoting hazards on long markdown.
        body_path = worktree.path / ".vivify-pr-body.md"
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_text(body, encoding="utf-8")

        cmd: list[str] = [
            "gh", "pr", "create",
            "--base", base_branch,
            "--head", worktree.branch,
            "--title", title,
            "--body-file", str(body_path),
        ]
        for label in labels:
            cmd.extend(["--label", label])
        if is_draft:
            cmd.append("--draft")

        result = _run(cmd, cwd=worktree.path, timeout=self.config.gh_timeout_seconds)

        # Tolerate missing labels on the remote: if ``gh`` rejects the PR
        # creation because one of the requested labels does not exist, retry
        # once with all ``--label`` arguments stripped. This avoids hard
        # failures on fresh repos that haven't had vivify labels created yet.
        if result.returncode != 0 and _looks_like_missing_label(result.stderr):
            logger.warning(
                "Label not found on remote, retrying gh pr create without labels: %s",
                (result.stderr or "").strip()[:300],
            )
            cmd_retry: list[str] = []
            skip_next = False
            for arg in cmd:
                if skip_next:
                    skip_next = False
                    continue
                if arg == "--label":
                    skip_next = True
                    continue
                cmd_retry.append(arg)
            result = _run(
                cmd_retry, cwd=worktree.path,
                timeout=self.config.gh_timeout_seconds,
            )
            if result.returncode == 0:
                # Reflect the retry outcome in the returned PullRequest.
                labels = []

        try:
            body_path.unlink(missing_ok=True)
        except Exception:  # pragma: no cover — cleanup only
            pass

        if result.returncode != 0:
            raise RuntimeError(
                f"gh pr create failed for {worktree.branch}: "
                f"{result.stderr.strip()[:400]}"
            )

        number, url = _parse_pr_url(result.stdout or result.stderr or "")
        logger.info(
            "Opened PR #%s (%s) draft=%s labels=%s",
            number, url, is_draft, labels,
        )
        return PullRequest(
            number=number,
            url=url,
            branch=worktree.branch,
            base=base_branch,
            draft=is_draft,
            labels=tuple(labels),
        )

    def push_and_open(
        self,
        worktree: Worktree,
        *,
        title: str,
        body: str,
        decision: GuardDecision | None = None,
        extra_labels: Sequence[str] = (),
        draft: Optional[bool] = None,
        base: Optional[str] = None,
    ) -> PullRequest:
        """Convenience: push then open a PR in a single call."""
        self.push_branch(worktree)
        return self.open_pr(
            worktree,
            title=title,
            body=body,
            decision=decision,
            extra_labels=extra_labels,
            draft=draft,
            base=base,
        )


__all__ = ["PrCreator", "PrCreatorConfig", "PullRequest"]
