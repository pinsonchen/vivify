"""Shared helpers for builtin fixers — subprocess + git interactions.

Builtin fixers operate inside a workspace path provided by the kernel (a git
worktree created by ``pr_mode.worktree``). They only stage / commit changes
inside that worktree; pushing and PR creation are the kernel's job.
"""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from vivify.interfaces.fixer import Fixer, FixContext
from vivify.models import FixResult, Issue

logger = logging.getLogger(__name__)


@dataclass
class _CmdResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


def run_cmd(
    cmd: Sequence[str] | str,
    *,
    cwd: Path | str,
    timeout: int = 300,
    shell: bool = False,
) -> _CmdResult:
    """Thin subprocess wrapper used by every builtin fixer."""
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd if shell else list(cmd),
            shell=shell,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return _CmdResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_seconds=time.time() - t0,
        )
    except subprocess.TimeoutExpired as e:
        return _CmdResult(
            returncode=-1,
            stdout=(e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
            stderr=f"timeout after {timeout}s",
            duration_seconds=time.time() - t0,
        )


def has_command(name: str) -> bool:
    """``True`` if ``name`` is on PATH (used by ``can_fix`` heuristics)."""
    import shutil
    return shutil.which(name) is not None


def list_changed_files(workspace: Path) -> list[str]:
    """Return paths changed in the working tree relative to ``workspace``."""
    res = run_cmd(["git", "status", "--porcelain"], cwd=workspace, timeout=15)
    out: list[str] = []
    for line in (res.stdout or "").splitlines():
        # porcelain v1: "XY path" or "XY orig -> path"
        if not line.strip():
            continue
        rest = line[3:] if len(line) > 3 else ""
        if "->" in rest:
            rest = rest.split("->", 1)[1].strip()
        out.append(rest.strip())
    return out


def stage_and_commit(
    workspace: Path,
    *,
    paths: Iterable[str] | None = None,
    message: str,
) -> tuple[bool, str]:
    """Stage ``paths`` (or all) and commit. Returns ``(ok, commit_sha_or_msg)``.

    A no-op commit (nothing staged) returns ``(False, "no changes to commit")``.
    """
    if paths is not None:
        paths = [p for p in paths if p]
        if not paths:
            return False, "no changes to commit"
        add = run_cmd(["git", "add", "--", *paths], cwd=workspace, timeout=30)
    else:
        add = run_cmd(["git", "add", "-A"], cwd=workspace, timeout=30)
    if add.returncode != 0:
        return False, f"git add failed: {add.stderr.strip()[:200]}"

    diff = run_cmd(["git", "diff", "--cached", "--quiet"], cwd=workspace, timeout=15)
    if diff.returncode == 0:
        return False, "no changes to commit"

    commit = run_cmd(["git", "commit", "-m", message], cwd=workspace, timeout=30)
    if commit.returncode != 0:
        return False, f"git commit failed: {commit.stderr.strip()[:200]}"
    sha = run_cmd(["git", "rev-parse", "HEAD"], cwd=workspace, timeout=10)
    return True, (sha.stdout.strip() or "committed")


def fail(message: str, *, duration: float = 0.0) -> FixResult:
    return FixResult(fixed=False, message=message, duration_seconds=duration)


def success(
    *,
    message: str,
    changed_files: list[str],
    duration: float,
    commit_hash: str | None = None,
    artifacts: dict | None = None,
) -> FixResult:
    return FixResult(
        fixed=True,
        message=message,
        changed_files=changed_files,
        duration_seconds=duration,
        commit_hash=commit_hash,
        artifacts=artifacts or {},
    )


class BaseFixer(Fixer):
    """Convenience base — concrete fixers only override ``handles_categories``,
    ``can_fix`` (default: category match), and ``fix``."""

    def can_fix(self, issue: Issue, ctx: FixContext) -> bool:
        return issue.category in self.handles_categories
