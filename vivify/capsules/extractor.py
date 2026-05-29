"""Capsule extraction — distills fix strategies from successful repairs.

Pure rule-driven; no LLM call. Designed to keep capsule generation cheap
enough to run on every successful agent fix.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from vivify.capsules.models import SkillCapsule


# Truncation defaults keep the capsule readable while bounding disk usage.
_STRATEGY_MAX_LEN = 500
_PROMPT_MAX_LEN = 800


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _summarise(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    # Try to cut at a sentence boundary if possible.
    truncated = text[:max_len]
    last_break = max(truncated.rfind(". "), truncated.rfind("\n"))
    if last_break >= int(max_len * 0.6):
        truncated = truncated[: last_break + 1]
    return truncated.rstrip() + "…"


def _extract_strategy(action_log: dict, issue: dict, fix_diff: str) -> str:
    """Pick the most informative human-readable summary available."""
    candidates = [
        action_log.get("result_summary"),
        (action_log.get("details") or {}).get("strategy"),
        (action_log.get("details") or {}).get("approach"),
        issue.get("description"),
        issue.get("title"),
    ]
    for c in candidates:
        text = _coerce_str(c).strip()
        if text:
            return _summarise(text, _STRATEGY_MAX_LEN)
    # Fallback to the diff's first non-empty hunk header / line.
    for line in (fix_diff or "").splitlines():
        line = line.strip()
        if line and not line.startswith(("+++", "---")):
            return _summarise(line, _STRATEGY_MAX_LEN)
    return "Apply a similar approach to the previous successful fix."


def _build_trigger_pattern(probe_id: str, issue_category: str, issue: dict) -> str:
    """Combine probe id, category and a few keywords from the issue text."""
    base = f"{probe_id}:{issue_category}".strip(":")
    title = _coerce_str(issue.get("title"))
    # Keep a handful of meaningful keywords from the title for fuzzy matching.
    tokens = [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", title)][:6]
    if tokens:
        base = f"{base} {' '.join(tokens)}"
    return base


def _build_prompt_template(strategy: str, probe_id: str, issue_category: str) -> str:
    header = "Previously this type of issue was fixed by:"
    suffix = "Apply a similar approach where applicable."
    body = strategy.strip() or suffix
    template = (
        f"{header}\n  - {body}\n"
        f"(captured from prior successful fix; "
        f"probe={probe_id or 'n/a'}, category={issue_category or 'n/a'}). "
        f"{suffix}"
    )
    return _summarise(template, _PROMPT_MAX_LEN)


class CapsuleExtractor:
    """Build :class:`SkillCapsule` objects from successful repair logs."""

    def extract_from_fix(
        self,
        action_log: dict,
        issue: dict,
        fix_diff: str = "",
    ) -> SkillCapsule:
        """Extract a skill capsule from a successful fix.

        Args:
            action_log: The successful action log entry (dict-like).
            issue: The original issue that was fixed (dict-like).
            fix_diff: The git diff of the fix (optional context).

        Returns:
            A new :class:`SkillCapsule` populated with strategy and template.
        """
        action_log = dict(action_log or {})
        issue = dict(issue or {})

        probe_id = _coerce_str(
            issue.get("source_probe")
            or issue.get("probe_id")
            or (action_log.get("details") or {}).get("source_probe")
        )
        issue_category = _coerce_str(
            issue.get("category") or action_log.get("category")
        )

        strategy = _extract_strategy(action_log, issue, fix_diff)
        trigger_pattern = _build_trigger_pattern(probe_id, issue_category, issue)
        prompt_template = _build_prompt_template(strategy, probe_id, issue_category)

        source_action_id = _coerce_str(action_log.get("id") or action_log.get("run_id"))

        return SkillCapsule(
            capsule_id=uuid.uuid4().hex,
            trigger_pattern=trigger_pattern,
            fix_strategy=strategy,
            prompt_template=prompt_template,
            source_action_id=source_action_id,
            probe_id=probe_id,
            issue_category=issue_category,
        )


__all__ = ["CapsuleExtractor"]
