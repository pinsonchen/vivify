"""Process slot manager for the Qoder CLI agent.

The agent can produce many parallel coding sessions (one per worktree). Each
spawned ``qodercli`` is tagged with the env variable ``AUTO_HEAL_AGENT=1`` so
this module can count *only the agent's own* subprocesses (ignoring an IDE the
user happens to have open) and gate new launches with a configurable cap.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Env tag that every agent-launched subprocess MUST set (see QoderCliAgent.heal).
AGENT_ENV_TAG = "AUTO_HEAL_AGENT"


def count_agent_processes(binary_basename: str = "qodercli") -> int:
    """Return the number of running agent processes started by auto-heal.

    We do not rely on cmdline matching alone because that picks up unrelated
    user invocations of ``qodercli``. Instead we scan ``/proc/<pid>/environ``
    for our env tag (Linux) and fall back to ``ps -E`` heuristics on macOS.
    """
    candidate_pids = _find_pids_by_name(binary_basename)
    if not candidate_pids:
        return 0
    count = 0
    for pid in candidate_pids:
        if _proc_has_env_tag(pid):
            count += 1
    return count


def wait_for_slot(
    *,
    category: str,
    max_concurrent: int,
    timeout_seconds: int = 300,
    poll_interval_seconds: int = 15,
    binary_basename: str = "qodercli",
) -> bool:
    """Block until a slot is free or ``timeout_seconds`` elapses.

    Returns ``True`` when a slot is available, ``False`` on timeout. The kernel
    treats ``False`` as "skip this round" and emits an action log entry.
    """
    if max_concurrent <= 0:
        return True
    start = time.time()
    while time.time() - start < timeout_seconds:
        current = count_agent_processes(binary_basename)
        if current < max_concurrent:
            return True
        logger.info(
            "[%s] agent processes (%d) at cap (%d), waiting %ds for slot...",
            category, current, max_concurrent, poll_interval_seconds,
        )
        time.sleep(poll_interval_seconds)
    return False


# ── internals ────────────────────────────────────────────────────────────────
def _find_pids_by_name(name: str) -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-f", name],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    out: list[int] = []
    for token in result.stdout.split():
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out


def _proc_has_env_tag(pid: int) -> bool:
    """True if the process was spawned with ``AUTO_HEAL_AGENT=1``."""
    # Linux fast path
    environ_path = f"/proc/{pid}/environ"
    if os.path.exists(environ_path):
        try:
            with open(environ_path, "rb") as f:
                data = f.read()
            return f"{AGENT_ENV_TAG}=1".encode() in data
        except (OSError, PermissionError):
            return False
    # macOS / others: best-effort `ps -E` (prints env after cmd).
    try:
        result = subprocess.run(
            ["ps", "-E", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return f"{AGENT_ENV_TAG}=1" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


__all__ = ["AGENT_ENV_TAG", "count_agent_processes", "wait_for_slot"]
