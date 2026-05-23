"""Git worktree manager — isolated dev environments per Issue / FeatureRequest.

Each fix / feature gets its own ``git worktree`` checked out from the latest
default branch under ``<repo>/.auto-heal/worktrees/<slug>/``. The worktree
owns a feature branch (``<prefix><slug>-<timestamp>``) that the kernel later
pushes and turns into a PR.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Worktree:
    path: Path
    branch: str
    base_ref: str  # e.g. ``origin/main`` or ``HEAD``


def _slugify(text: str, *, max_len: int = 48) -> str:
    """Convert a free-form title to a filesystem / branch-safe slug."""
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len] or "task"


def _run(cmd, cwd, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd), cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


class WorktreeManager:
    """Create, list, and remove auto-heal worktrees for a repo."""

    def __init__(
        self,
        repo_root: Path | str,
        *,
        worktree_base: Path | str | None = None,
        branch_prefix: str = "auto-heal/",
        base_branch: str = "main",
        fetch_before_create: bool = True,
    ):
        self.repo_root = Path(repo_root)
        self.worktree_base = (
            Path(worktree_base)
            if worktree_base
            else self.repo_root / ".auto-heal" / "worktrees"
        )
        self.branch_prefix = branch_prefix
        self.base_branch = base_branch
        self.fetch_before_create = fetch_before_create

    # ── public API ──────────────────────────────────────────────────────────
    def create(self, slug_hint: str, *, base_ref: Optional[str] = None) -> Worktree:
        """Create a fresh worktree off the latest base branch."""
        self.worktree_base.mkdir(parents=True, exist_ok=True)
        slug = _slugify(slug_hint)
        ts = int(time.time())
        branch = f"{self.branch_prefix}{slug}-{ts}"
        path = self.worktree_base / f"{slug}-{ts}"

        if path.exists():
            logger.warning("Worktree path already exists, removing: %s", path)
            self._cleanup_path(path)

        # Always work from a fresh remote view of the base branch.
        ref = base_ref or self._resolve_base_ref()

        result = _run(
            ["git", "worktree", "add", "-b", branch, str(path), ref],
            cwd=self.repo_root, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git worktree add failed (ref={ref}): {result.stderr.strip()[:300]}"
            )
        logger.info("Created worktree %s on branch %s (from %s)", path, branch, ref)
        return Worktree(path=path, branch=branch, base_ref=ref)

    def remove(self, worktree: Worktree, *, delete_branch: bool = True) -> None:
        """Remove the worktree directory and optionally delete the local branch."""
        result = _run(
            ["git", "worktree", "remove", "--force", str(worktree.path)],
            cwd=self.repo_root, timeout=60,
        )
        if result.returncode != 0:
            logger.warning(
                "git worktree remove failed for %s: %s",
                worktree.path, result.stderr.strip()[:200],
            )
            self._cleanup_path(worktree.path)
        if delete_branch:
            _run(
                ["git", "branch", "-D", worktree.branch],
                cwd=self.repo_root, timeout=30,
            )

    def list(self) -> list[Worktree]:
        """Return auto-heal worktrees currently registered with git."""
        result = _run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.repo_root, timeout=30,
        )
        if result.returncode != 0:
            return []
        out: list[Worktree] = []
        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                if current.get("worktree"):
                    out.append(
                        Worktree(
                            path=Path(current["worktree"]),
                            branch=current.get("branch", "").replace("refs/heads/", ""),
                            base_ref=current.get("HEAD", ""),
                        )
                    )
                current = {}
                continue
            if " " in line:
                k, v = line.split(" ", 1)
                current[k] = v
            else:
                current[line] = ""
        if current.get("worktree"):
            out.append(
                Worktree(
                    path=Path(current["worktree"]),
                    branch=current.get("branch", "").replace("refs/heads/", ""),
                    base_ref=current.get("HEAD", ""),
                )
            )
        return [w for w in out if str(w.path).startswith(str(self.worktree_base))]

    # ── internals ───────────────────────────────────────────────────────────
    def _resolve_base_ref(self) -> str:
        if self.fetch_before_create:
            _run(["git", "fetch", "--quiet", "origin", self.base_branch],
                 cwd=self.repo_root, timeout=60)
        # Prefer the remote tracking ref if it exists; fall back to local branch.
        if self._ref_exists(f"origin/{self.base_branch}"):
            return f"origin/{self.base_branch}"
        if self._ref_exists(self.base_branch):
            return self.base_branch
        return "HEAD"

    def _ref_exists(self, ref: str) -> bool:
        return _run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=self.repo_root, timeout=10,
        ).returncode == 0

    def _cleanup_path(self, path: Path) -> None:
        if path.exists():
            try:
                shutil.rmtree(path, ignore_errors=True)
            except Exception as e:
                logger.warning("rmtree fallback failed for %s: %s", path, e)


__all__ = ["Worktree", "WorktreeManager"]
