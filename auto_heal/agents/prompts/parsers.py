"""Parsers for structured agent output.

Ports the five parsers from ``channels-monitor/auto_heal/feature_dev.py`` —
project-agnostic versions that operate on raw stdout from any
:class:`CodingAgent`. The agent is asked (via prompt templates) to emit JSON
fenced blocks; these parsers locate the *last* matching block and tolerate
malformed neighbours.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

# Anywhere a parser asks the agent for a JSON block we use the canonical
# ```json\n...\n``` fence. The greedy DOTALL regex captures everything between
# the first ``` and the closing ``` on its own line.
_FENCED_JSON_RE = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)


# ── helpers ──────────────────────────────────────────────────────────────────
def _iter_fenced_json(output: str, *, must_contain_key: Optional[str] = None) -> Iterable[dict]:
    """Yield every ``{...}`` decoded from a fenced ```json``` block.

    Skips malformed blocks. When ``must_contain_key`` is given the source text
    must contain ``"<key>"`` to be considered (cheap pre-filter avoids false
    positives when multiple JSON blocks appear).
    """
    for raw in _FENCED_JSON_RE.findall(output or ""):
        if must_contain_key and f'"{must_contain_key}"' not in raw:
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            yield parsed


def _last_fenced_json(output: str, *, must_contain_key: Optional[str] = None) -> Optional[dict]:
    """Return the *last* fenced JSON block, or ``None`` if absent."""
    last: Optional[dict] = None
    for parsed in _iter_fenced_json(output, must_contain_key=must_contain_key):
        last = parsed
    return last


# ── individual parsers ───────────────────────────────────────────────────────
def parse_evaluation_result(output: str) -> dict:
    """Parse the JSON returned by ``feature_evaluate`` prompts."""
    result: dict[str, Any] = {
        "priority": None,
        "feasible": True,
        "feasibility": "",
        "summary": "",
        "needs_admin_review": False,
        "estimated_effort": None,
        "implementation_approach": "",
    }
    if not output:
        return result

    parsed = _last_fenced_json(output, must_contain_key="priority")
    if parsed:
        result["priority"] = parsed.get("priority", "P2")
        result["feasible"] = parsed.get("feasible", True)
        result["feasibility"] = parsed.get("feasibility", "")
        result["summary"] = parsed.get("summary", "")
        result["needs_admin_review"] = parsed.get("needs_admin_review", False)
        result["estimated_effort"] = json.dumps(
            {
                "hours": parsed.get("estimated_effort_hours", 0),
                "files": parsed.get("affected_files_count", 0),
                "complexity": parsed.get("technical_complexity", "medium"),
                "risks": parsed.get("risks", []),
            },
            ensure_ascii=False,
        )
        result["implementation_approach"] = parsed.get("implementation_approach", "")
        return result

    # Fallback: bare JSON object
    bare = re.search(r'\{[^{}]*"priority"[^{}]*\}', output)
    if bare:
        try:
            parsed = json.loads(bare.group())
        except (TypeError, ValueError):
            return result
        result["priority"] = parsed.get("priority", "P2")
        result["feasible"] = parsed.get("feasible", True)
        result["feasibility"] = parsed.get("feasibility", "")
        result["summary"] = parsed.get("summary", "")
        result["needs_admin_review"] = parsed.get("needs_admin_review", False)
    return result


def parse_verification_result(output: str) -> dict:
    """Parse the JSON returned by ``feature_verify`` / ``fix_verify`` prompts.

    Returns ``parse_failed: True`` when no usable block is present so callers
    can distinguish "agent said failed" from "agent didn't speak the protocol".
    """
    default = {
        "verified": False,
        "summary": "verification result could not be parsed",
        "issues": ["unable to parse verification output"],
        "parse_failed": True,
    }
    if not output:
        return default

    for parsed in reversed(list(_iter_fenced_json(output, must_contain_key="verified"))):
        if "verified" in parsed:
            return {
                "verified": bool(parsed.get("verified", False)),
                "summary": parsed.get("summary", ""),
                "issues": list(parsed.get("issues", []) or []),
            }

    bare = re.search(r'\{[^{}]*"verified"[^{}]*\}', output, re.DOTALL)
    if bare:
        try:
            parsed = json.loads(bare.group())
        except (TypeError, ValueError):
            return default
        return {
            "verified": bool(parsed.get("verified", False)),
            "summary": parsed.get("summary", ""),
            "issues": list(parsed.get("issues", []) or []),
        }
    return default


def parse_next_steps(output: str) -> list[dict]:
    """Parse the ``next_steps`` block emitted at the end of develop / fix prompts.

    Returns a list of ``{"title", "description"}`` dicts (max 3, trimmed).
    """
    if not output:
        return []
    candidate = _last_fenced_json(output, must_contain_key="next_steps")
    if candidate is None:
        m = re.search(
            r'\{[^{}]*"next_steps"\s*:\s*\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\][^{}]*\}',
            output, re.DOTALL,
        )
        if m:
            try:
                candidate = json.loads(m.group())
            except (TypeError, ValueError):
                return []
    if not isinstance(candidate, dict):
        return []
    steps = candidate.get("next_steps") or []
    if not isinstance(steps, list):
        return []
    cleaned: list[dict] = []
    for step in steps[:3]:
        if not isinstance(step, dict):
            continue
        title = (step.get("title") or "").strip()
        desc = (step.get("description") or "").strip()
        if title and desc:
            cleaned.append({"title": title[:200], "description": desc[:2000]})
    return cleaned


def parse_skipped_ids(output: str) -> set[int]:
    """Parse skipped feature IDs from a batch-develop run.

    Recognises both:

    * Inline annotations: ``skipped: #42 reason: <text>``
    * ``skipped_ids: [42, 43]`` inside a fenced JSON block
    """
    if not output:
        return set()
    skipped: set[int] = set()

    for m in re.finditer(r"skipped\s*[::]\s*#(\d+)", output, re.IGNORECASE):
        try:
            skipped.add(int(m.group(1)))
        except (TypeError, ValueError):
            continue

    for parsed in reversed(list(_iter_fenced_json(output, must_contain_key="skipped_ids"))):
        ids = parsed.get("skipped_ids")
        if isinstance(ids, list):
            for x in ids:
                try:
                    skipped.add(int(x))
                except (TypeError, ValueError):
                    continue
            break
    return skipped


def parse_commit_info(output: str, *, repo_url: Optional[str] = None) -> dict:
    """Parse a commit hash from ``git commit`` / ``git push`` echo output.

    ``repo_url`` (e.g. ``https://github.com/owner/name``) is used to build a
    convenience ``commit_url`` — pass ``None`` if you don't want one.
    """
    result: dict[str, Optional[str]] = {"commit_hash": None, "commit_url": None}
    if not output:
        return result

    # ``[main abcdef0] commit message`` style
    m = re.search(r"\[(?:main|master|[a-zA-Z0-9_/-]+)\s+([0-9a-f]{7,40})\]", output)
    if m:
        sha = m.group(1)
        result["commit_hash"] = sha
        if repo_url:
            result["commit_url"] = f"{repo_url.rstrip('/')}/commit/{sha}"
        return result

    # ``abc1234..def5678 branch -> branch`` style (after push)
    m = re.search(r"([0-9a-f]{7,40})\.\.([0-9a-f]{7,40})\s+\S+\s*->\s*\S+", output)
    if m:
        sha = m.group(2)
        result["commit_hash"] = sha
        if repo_url:
            result["commit_url"] = f"{repo_url.rstrip('/')}/commit/{sha}"
    return result


__all__ = [
    "parse_evaluation_result",
    "parse_verification_result",
    "parse_next_steps",
    "parse_skipped_ids",
    "parse_commit_info",
]
