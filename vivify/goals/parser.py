"""Parse ``GOALS.md`` into validated :class:`Goal` objects.

Format (see plan §5):

```
---
version: 1
owner: "@team"
review_cadence: weekly
---

# Project Goals

## Goal: <goal name>
<free-form description, may span lines>

- KPI: <name> target=<expr> direction=<up|down|stable> unit=<text>
- Deadline: 2025-Q1
- Notes: <free text>

## Goal: <next goal>
...
```

The optional YAML front-matter is parsed with PyYAML when available and
otherwise ignored. Any malformed goal raises :class:`ValueError` with a
human-readable message so ``vivify goals show`` surfaces it cleanly.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Iterator, Optional

from vivify.models.feature import KPI, Goal

logger = logging.getLogger(__name__)


_GOAL_HEADER_RE = re.compile(r"^##\s+Goal:\s*(.+?)\s*$", re.MULTILINE)
_KPI_LINE_RE = re.compile(
    r"^-\s*KPI:\s*(?P<name>[A-Za-z0-9_.-]+)"
    r"\s+target=(?P<target>\S+)"
    r"(?:\s+direction=(?P<direction>up|down|stable))?"
    r"(?:\s+unit=(?P<unit>\S+))?\s*$"
)
_DEADLINE_RE = re.compile(r"^-\s*Deadline:\s*(.+?)\s*$")
_NOTES_RE = re.compile(r"^-\s*Notes:\s*(.+?)\s*$")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


@dataclass
class GoalsDoc:
    """Parsed ``GOALS.md`` — front-matter metadata plus a list of goals."""
    version: int = 1
    owner: str = ""
    review_cadence: str = ""
    goals: list[Goal] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.goals is None:
            self.goals = []


def _strip_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    front = m.group(1)
    body = text[m.end():]
    meta: dict = {}
    try:
        import yaml  # type: ignore[import-untyped]
        loaded = yaml.safe_load(front) or {}
        if isinstance(loaded, dict):
            meta = loaded
    except Exception:
        # fall back to naive ``key: value`` parsing
        for line in front.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"\'')
    return meta, body


def _parse_deadline(raw: str) -> Optional[date]:
    s = raw.strip()
    # Accept YYYY-MM-DD or YYYY-Qn (best-effort)
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    m = re.match(r"^(\d{4})-Q([1-4])$", s)
    if m:
        year = int(m.group(1))
        quarter = int(m.group(2))
        month = quarter * 3
        # last day of the quarter — pick day 28 to avoid month-length drama
        return date(year, month, 28)
    return None


def _iter_goal_blocks(body: str) -> Iterator[tuple[str, str]]:
    """Yield ``(name, raw_block)`` pairs for each ``## Goal:`` heading."""
    matches = list(_GOAL_HEADER_RE.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        yield m.group(1).strip(), body[m.end():end]


def _parse_goal_block(name: str, block: str) -> Goal:
    description_parts: list[str] = []
    kpis: list[KPI] = []
    deadline: Optional[date] = None
    notes: str = ""

    saw_list = False
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            saw_list = True
            kpi_match = _KPI_LINE_RE.match(stripped)
            if kpi_match:
                kpis.append(
                    KPI(
                        name=kpi_match.group("name"),
                        target=kpi_match.group("target"),
                        direction=kpi_match.group("direction") or "up",
                        unit=(kpi_match.group("unit") or "").strip('"\''),
                    )
                )
                continue
            dl = _DEADLINE_RE.match(stripped)
            if dl:
                deadline = _parse_deadline(dl.group(1))
                continue
            nt = _NOTES_RE.match(stripped)
            if nt:
                notes = nt.group(1)
                continue
            # Unknown bullet — keep it in description for human reviewers.
            description_parts.append(stripped)
        elif not saw_list:
            description_parts.append(stripped)

    if not kpis:
        raise ValueError(
            f"Goal '{name}' has no KPI lines. Add at least one "
            f"`- KPI: <name> target=<expr> direction=up|down|stable [unit=<text>]`."
        )
    return Goal(
        name=name,
        description=" ".join(description_parts).strip(),
        kpis=tuple(kpis),
        deadline=deadline,
        notes=notes,
    )


def parse_goals(md_text: str) -> GoalsDoc:
    """Parse a ``GOALS.md`` document. Raises :class:`ValueError` on malformed input."""
    meta, body = _strip_frontmatter(md_text or "")
    goals: list[Goal] = []
    errors: list[str] = []
    for name, block in _iter_goal_blocks(body):
        try:
            goals.append(_parse_goal_block(name, block))
        except ValueError as e:
            errors.append(str(e))
    if errors:
        raise ValueError("Goals parse errors:\n  - " + "\n  - ".join(errors))
    if not goals:
        # An empty file is valid (nothing to do); only complain on missing
        # headings if non-trivial body present.
        if body.strip() and "## Goal:" not in body:
            raise ValueError(
                "GOALS.md contains content but no '## Goal:' headings. "
                "Add at least one goal section."
            )
    try:
        version = int(meta.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    return GoalsDoc(
        version=version,
        owner=str(meta.get("owner", "") or ""),
        review_cadence=str(meta.get("review_cadence", "") or ""),
        goals=goals,
    )


def parse_goal_list(md_text: str) -> list[Goal]:
    """Backward-compatible helper returning only the goals list."""
    return parse_goals(md_text).goals


__all__ = ["GoalsDoc", "parse_goals", "parse_goal_list"]
