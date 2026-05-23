"""Pre-PR quality gate — cheap checks run inside a worktree before pushing.

Ported and generalised from ``feature_dev._quick_quality_check``. The gate
runs **only** in the worktree and is allowed to be lenient: a failure here
means "don't open the PR yet — surface a follow-up issue", not "abort the run".
"""
from __future__ import annotations

import logging
import py_compile
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)


@dataclass
class QualityCheckResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.passed and not self.warnings:
            return "all quality checks passed"
        if self.passed:
            return f"passed with {len(self.warnings)} warnings"
        return f"{len(self.errors)} errors: " + "; ".join(self.errors[:3])


def _run(cmd: Sequence[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd), cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


def _modified_paths(workspace: Path, *, against: str, pattern: str) -> list[str]:
    """``git diff --name-only <against>`` filtered by pattern (relative paths)."""
    res = _run(
        ["git", "diff", "--name-only", against, "--", pattern],
        cwd=workspace, timeout=30,
    )
    if res.returncode != 0:
        return []
    return [p for p in res.stdout.splitlines() if p.strip()]


def run_quality_checks(
    workspace: Path | str,
    *,
    base_ref: str = "origin/main",
    run_pytest: bool = False,
    pytest_args: Sequence[str] = ("-q", "--maxfail=5"),
    test_command: Iterable[str] | None = None,
) -> QualityCheckResult:
    """Run the pre-PR gate.

    Always runs:

    * Python syntax (``py_compile``) on every modified ``.py``.
    * ``ruff check`` on modified Python files (if available).
    * ``tsc --noEmit`` if the worktree has ``frontend/node_modules``.

    Opt-in:

    * ``run_pytest=True`` → ``pytest`` (use ``test_command`` to override).
    """
    ws = Path(workspace)
    result = QualityCheckResult(passed=True)

    # ── Python syntax check ──
    py_files = _modified_paths(ws, against=base_ref, pattern="*.py")
    for rel in py_files:
        full = ws / rel
        if not full.exists():
            continue
        try:
            py_compile.compile(str(full), doraise=True)
        except py_compile.PyCompileError as e:
            result.errors.append(f"python syntax: {rel}: {str(e)[:160]}")
            result.passed = False
        except Exception as e:
            result.warnings.append(f"py_compile {rel} skipped: {e}")

    # ── ruff (modified files only) ──
    if py_files and _has_command("ruff"):
        res = _run(["ruff", "check", "--output-format", "concise", *py_files],
                   cwd=ws, timeout=120)
        if res.returncode != 0 and res.stdout.strip():
            errs = res.stdout.strip().splitlines()[:5]
            result.errors.append("ruff: " + "; ".join(errs))
            result.passed = False

    # ── TypeScript (frontend) ──
    frontend = ws / "frontend"
    if frontend.is_dir() and (frontend / "node_modules").is_dir() and _has_command("npx"):
        res = _run(["npx", "--no-install", "tsc", "--noEmit"],
                   cwd=frontend, timeout=120)
        if res.returncode != 0:
            errs = res.stdout.strip().splitlines()[:5]
            result.errors.append("tsc: " + "; ".join(errs))
            result.passed = False

    # ── Project test suite (opt-in) ──
    if run_pytest:
        cmd = list(test_command) if test_command else ["pytest", *pytest_args]
        if _has_command(cmd[0]):
            res = _run(cmd, cwd=ws, timeout=900)
            if res.returncode != 0:
                tail = (res.stdout or "").strip().splitlines()[-5:]
                result.errors.append("tests failed: " + "; ".join(tail))
                result.passed = False
        else:
            result.warnings.append(f"{cmd[0]} not on PATH; tests skipped")

    return result


def _has_command(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


__all__ = ["QualityCheckResult", "run_quality_checks"]
