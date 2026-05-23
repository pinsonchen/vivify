"""Tests for ``vivify.goals.parser``."""
from __future__ import annotations

from datetime import date

import pytest

from vivify.goals.parser import GoalsDoc, parse_goal_list, parse_goals


GOLDEN = """\
---
version: 1
owner: "@team-platform"
review_cadence: weekly
---

# Project Goals

## Goal: Reduce CI flakiness
让 CI 套件可信，让开发者相信失败都是真实失败。

- KPI: ci_pass_rate target=>=98% direction=up unit=%
- KPI: median_ci_duration target=<=8min direction=down unit=min
- Deadline: 2025-Q1
- Notes: 重点优化集成测试；禁止 silently skip。

## Goal: Increase test coverage
- KPI: line_coverage target=>=80% direction=up unit=%
- KPI: branch_coverage target=>=70% direction=up unit=%
- Deadline: 2025-06-30
"""


def test_parse_golden_document():
    doc = parse_goals(GOLDEN)
    assert isinstance(doc, GoalsDoc)
    assert doc.version == 1
    assert doc.owner == "@team-platform"
    assert doc.review_cadence == "weekly"
    assert len(doc.goals) == 2

    g1 = doc.goals[0]
    assert g1.name == "Reduce CI flakiness"
    assert "让 CI 套件可信" in g1.description
    assert len(g1.kpis) == 2
    assert g1.kpis[0].name == "ci_pass_rate"
    assert g1.kpis[0].target == ">=98%"
    assert g1.kpis[0].direction == "up"
    assert g1.kpis[0].unit == "%"
    assert g1.kpis[1].direction == "down"
    assert g1.deadline == date(2025, 3, 28)  # Q1 → month=3, day=28
    assert "silently skip" in g1.notes

    g2 = doc.goals[1]
    assert g2.name == "Increase test coverage"
    assert g2.deadline == date(2025, 6, 30)


def test_missing_kpi_raises():
    md = "## Goal: Empty\nNo KPI here.\n"
    with pytest.raises(ValueError, match="no KPI lines"):
        parse_goals(md)


def test_empty_document_returns_empty():
    doc = parse_goals("")
    assert doc.goals == []


def test_body_without_goal_heading_raises():
    md = "Some random text without any goal heading at all.\n"
    with pytest.raises(ValueError, match="no '## Goal:' headings"):
        parse_goals(md)


def test_default_direction_is_up():
    md = (
        "## Goal: Quality\n"
        "Improve quality.\n"
        "- KPI: bug_count target=0\n"
    )
    doc = parse_goals(md)
    assert doc.goals[0].kpis[0].direction == "up"
    assert doc.goals[0].kpis[0].unit == ""


def test_multiple_kpi_errors_aggregated():
    md = (
        "## Goal: A\nDesc.\n"  # missing KPI
        "## Goal: B\nDesc.\n"  # also missing
    )
    with pytest.raises(ValueError) as exc:
        parse_goals(md)
    msg = str(exc.value)
    assert "Goal 'A'" in msg
    assert "Goal 'B'" in msg


def test_quarter_deadlines():
    md = (
        "## Goal: Q-test\n"
        "- KPI: x target=1\n"
        "- Deadline: 2025-Q4\n"
    )
    doc = parse_goals(md)
    assert doc.goals[0].deadline == date(2025, 12, 28)


def test_iso_deadline():
    md = (
        "## Goal: Iso\n"
        "- KPI: x target=1\n"
        "- Deadline: 2026-01-15\n"
    )
    doc = parse_goals(md)
    assert doc.goals[0].deadline == date(2026, 1, 15)


def test_invalid_deadline_returns_none():
    md = (
        "## Goal: Bad\n"
        "- KPI: x target=1\n"
        "- Deadline: someday\n"
    )
    doc = parse_goals(md)
    assert doc.goals[0].deadline is None


def test_no_frontmatter_defaults():
    md = (
        "## Goal: Plain\n"
        "- KPI: x target=1\n"
    )
    doc = parse_goals(md)
    assert doc.version == 1
    assert doc.owner == ""
    assert doc.review_cadence == ""


def test_parse_goal_list_helper():
    goals = parse_goal_list(GOLDEN)
    assert len(goals) == 2
    assert goals[0].name == "Reduce CI flakiness"
