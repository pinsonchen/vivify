"""Compute a stable hash of the auto-heal package contents.

Used by the kernel to detect when the package's own files changed (e.g. because
a self-grow PR landed) and trigger a graceful restart.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

# Files that change without affecting runtime behaviour.
_IGNORE_NAMES = {"__pycache__", ".pyc", ".pyo", ".pyd"}
_IGNORE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _iter_files(root: Path, *, suffixes: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    suff_set = set(suffixes)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in _IGNORE_DIR_NAMES for part in p.parts):
            continue
        if p.suffix in _IGNORE_NAMES:
            continue
        if suff_set and p.suffix not in suff_set:
            continue
        out.append(p)
    return out


def compute_code_hash(
    package_root: Path | str,
    *,
    suffixes: Iterable[str] = (".py", ".yml", ".yaml", ".j2", ".sql"),
) -> str:
    """SHA-256 of all package source files (sorted, content + relative path)."""
    root = Path(package_root)
    if not root.exists():
        return ""
    h = hashlib.sha256()
    for fp in _iter_files(root, suffixes=suffixes):
        rel = fp.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(fp.read_bytes())
        except Exception:
            continue
        h.update(b"\0\0")
    return h.hexdigest()


__all__ = ["compute_code_hash"]
