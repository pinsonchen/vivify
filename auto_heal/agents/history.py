"""Recent-history loader for prompts.

When asking the agent to fix a recurring category of issue, we prepend the
last few attempts so the agent can avoid repeating ineffective strategies.
The ``channels-monitor`` original scanned log files; here we instead read
:class:`ActionLog` records from the :class:`StorageProvider` — they're already
indexed by category and survive log rotation.
"""
from __future__ import annotations

from typing import Optional

from auto_heal.interfaces.storage import StorageProvider


def load_history(
    storage: StorageProvider,
    category: str,
    *,
    max_entries: int = 5,
) -> str:
    """Return a markdown block summarising recent fix attempts for ``category``.

    Empty string when there's nothing useful — callers concatenate this into
    the prompt unconditionally.
    """
    if not category:
        return ""
    try:
        logs = storage.search_action_logs(category=category, limit=max(max_entries * 4, 20))
    except Exception:
        return ""

    # Keep only fix-style action types, newest first → trim to N → render oldest first.
    relevant = [
        log for log in logs
        if log.action_type in ("heal", "direct_fix", "feature_dev", "fix_issue")
    ][:max_entries]
    if not relevant:
        return ""
    relevant.reverse()

    lines: list[str] = [
        "## Recent fix attempts for this category",
        "Past attempts on the same category — review before retrying:",
        "",
    ]
    failed_count = 0
    for i, log in enumerate(relevant, 1):
        date = log.created_at.strftime("%Y-%m-%d") if log.created_at else "unknown"
        improved = "✓ improved" if log.improved else "⚠ unverified"
        if not log.improved:
            failed_count += 1
        lines.append(f"### Attempt {i} ({date})")
        lines.append(f"- Status: {log.status} ({improved})")
        if log.title:
            lines.append(f"- Issue: {log.title}")
        if log.result_summary:
            lines.append(f"- Result: {_truncate(log.result_summary, 240)}")
        lines.append("")

    if failed_count:
        lines.append(
            f"**Note**: {failed_count} of {len(relevant)} recent attempts did not "
            "improve the metric. Diagnose why the previous fix was ineffective and "
            "try a different strategy. If detection itself is wrong, propose changes "
            "to the relevant probe."
        )
        lines.append("")
    return "\n".join(lines)


def _truncate(s: Optional[str], n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


__all__ = ["load_history"]
