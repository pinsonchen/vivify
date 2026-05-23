"""Self-growth guard — classifies diffs touching auto-heal's own code.

When the agent decides to modify *auto-heal itself* (a "self-grow" PR), this
module decides:

* **plugin** PR — only whitelisted paths changed (probes, fixers, snippets,
  templates). Safe to auto-merge if the user opted in.
* **kernel** PR — kernel / interfaces / storage code changed. Forced to draft,
  labelled ``auto-heal:kernel-change``, and **never** auto-merged.
* **mixed** PR — both. Treated as kernel for safety.
* **external** — change does not touch the auto-heal package. No special
  handling.

Per plan §10: ``self_growth.kernel_modification`` of ``never_allowed`` causes
the kernel to abort the PR; ``pr_with_two_approvals`` opens a draft.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


class DiffClass(str, Enum):
    EXTERNAL = "external"
    PLUGIN = "plugin"
    KERNEL = "kernel"
    MIXED = "mixed"


# Paths that may be modified freely by the agent's self-improvement flow.
DEFAULT_PLUGIN_PATHS: tuple[str, ...] = (
    "auto_heal/probes/builtin/",
    "auto_heal/fixers/builtin/",
    "auto_heal/agents/prompts/templates/",
    "auto_heal/agents/prompts/snippets.py",
)

# Anything inside the auto-heal package not covered by PLUGIN_PATHS counts as
# a "kernel" change for guarding purposes.
KERNEL_PREFIX = "auto_heal/"


@dataclass
class GuardDecision:
    classification: DiffClass
    plugin_files: tuple[str, ...] = ()
    kernel_files: tuple[str, ...] = ()
    external_files: tuple[str, ...] = ()

    @property
    def labels(self) -> list[str]:
        if self.classification is DiffClass.KERNEL:
            return ["auto-heal:kernel-change"]
        if self.classification is DiffClass.MIXED:
            return ["auto-heal:kernel-change", "auto-heal:plugin-change"]
        if self.classification is DiffClass.PLUGIN:
            return ["auto-heal:plugin-change"]
        return []

    @property
    def force_draft(self) -> bool:
        return self.classification in (DiffClass.KERNEL, DiffClass.MIXED)

    @property
    def allow_auto_merge(self) -> bool:
        # Only pure plugin / external PRs may auto-merge (caller still gates on config).
        return self.classification in (DiffClass.PLUGIN, DiffClass.EXTERNAL)


def classify_diff(
    paths: Sequence[str],
    *,
    allowed_plugin_paths: Sequence[str] = DEFAULT_PLUGIN_PATHS,
) -> GuardDecision:
    """Pure function — classify a list of file paths."""
    plugin: list[str] = []
    kernel: list[str] = []
    external: list[str] = []
    norm_allowed = tuple(p.rstrip("/") for p in allowed_plugin_paths)
    for raw in paths:
        path = raw.strip()
        if not path:
            continue
        if path.startswith(KERNEL_PREFIX):
            if _matches_any(path, norm_allowed):
                plugin.append(path)
            else:
                kernel.append(path)
        else:
            external.append(path)
    if kernel and plugin:
        cls = DiffClass.MIXED
    elif kernel:
        cls = DiffClass.KERNEL
    elif plugin:
        cls = DiffClass.PLUGIN
    else:
        cls = DiffClass.EXTERNAL
    return GuardDecision(
        classification=cls,
        plugin_files=tuple(plugin),
        kernel_files=tuple(kernel),
        external_files=tuple(external),
    )


def classify_worktree(workspace: Path | str, *, base_ref: str = "origin/main") -> GuardDecision:
    """Run ``git diff --name-only`` and feed the result through :func:`classify_diff`."""
    res = subprocess.run(
        ["git", "diff", "--name-only", base_ref],
        cwd=str(workspace), capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        logger.warning("git diff failed: %s", res.stderr.strip()[:200])
        return GuardDecision(classification=DiffClass.EXTERNAL)
    return classify_diff([p for p in res.stdout.splitlines() if p.strip()])


def _matches_any(path: str, prefixes: Sequence[str]) -> bool:
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return True
        # exact-file allowance (e.g. ``snippets.py``)
        if re.fullmatch(re.escape(prefix), path):
            return True
    return False


__all__ = ["DiffClass", "GuardDecision", "classify_diff", "classify_worktree", "DEFAULT_PLUGIN_PATHS"]
