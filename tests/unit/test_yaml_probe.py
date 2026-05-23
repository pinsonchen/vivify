"""Tests for YAML probe parsing + analyze rules."""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from auto_heal.interfaces.probe import ProbeContext
from auto_heal.models import Issue, IssueLevel
from auto_heal.probes.base import YamlProbe


@pytest.fixture
def ctx(tmp_path):
    return ProbeContext(
        repo_root=tmp_path,
        config=MagicMock(),
        storage=MagicMock(),
        logger=logging.getLogger("test_probe"),
    )


def _builtin_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "auto_heal" / "probes" / "builtin"


def test_load_all_builtin_probes_have_valid_schema():
    yml_files = sorted(_builtin_dir().glob("*.yml"))
    assert yml_files, "expected at least one builtin probe yml"
    for path in yml_files:
        probe = YamlProbe.from_file(path)
        assert probe.id, f"{path.name}: missing id"
        assert isinstance(probe.enabled_by_default, bool)
        assert isinstance(probe.runs_on, tuple)


def test_analyze_emits_issue_when_rule_fires(ctx):
    probe = YamlProbe.from_yaml(
        """
id: synthetic_low_coverage
description: synthetic
analyze:
  rules:
    - when: "coverage_percent < 80"
      emit:
        category: test_coverage_low
        level: HIGH
        title: "Coverage {{ coverage_percent }}% below 80%"
        description: "Increase coverage."
        data:
          percent: "{{ coverage_percent }}"
"""
    )
    issues = probe.analyze({"coverage_percent": 55}, ctx)
    assert len(issues) == 1
    issue = issues[0]
    assert isinstance(issue, Issue)
    assert issue.category == "test_coverage_low"
    assert issue.level is IssueLevel.HIGH
    assert "55" in issue.title
    assert issue.data == {"percent": "55"}
    assert issue.source_probe == "synthetic_low_coverage"
    assert issue.hash  # factory computes a stable hash


def test_analyze_skips_when_rule_does_not_fire(ctx):
    probe = YamlProbe.from_yaml(
        """
id: synthetic
analyze:
  rules:
    - when: "coverage_percent < 80"
      emit:
        category: low
        level: HIGH
        title: low
"""
    )
    assert probe.analyze({"coverage_percent": 99}, ctx) == []


def test_invalid_level_falls_back_to_medium(ctx):
    probe = YamlProbe.from_yaml(
        """
id: bad_level
analyze:
  rules:
    - when: "x > 0"
      emit:
        category: cat
        level: NOT_A_LEVEL
        title: "t"
"""
    )
    issues = probe.analyze({"x": 1}, ctx)
    assert len(issues) == 1
    assert issues[0].level is IssueLevel.MEDIUM


def test_missing_id_raises():
    with pytest.raises(ValueError, match="missing required field: id"):
        YamlProbe.from_yaml("description: no id here\nrunsy: nope\n")


def test_top_level_must_be_mapping():
    with pytest.raises(ValueError, match="must be a mapping"):
        YamlProbe.from_yaml("- just a list\n")


def test_multiple_rules_can_fire(ctx):
    probe = YamlProbe.from_yaml(
        """
id: multi_rule
analyze:
  rules:
    - when: "cov < 80"
      emit: { category: warn, level: HIGH, title: "warn" }
    - when: "cov < 50"
      emit: { category: critical, level: CRITICAL, title: "crit" }
"""
    )
    issues = probe.analyze({"cov": 40}, ctx)
    cats = {i.category for i in issues}
    assert cats == {"warn", "critical"}


def test_yaml_probe_runs_collect(ctx):
    probe = YamlProbe.from_yaml(
        """
id: echo_probe
collect:
  steps:
    - shell: "echo 42"
      as: answer
      coerce: int
analyze:
  rules:
    - when: "answer == 42"
      emit: { category: ok, level: LOW, title: "answer={{ answer }}" }
"""
    )
    bindings = probe.collect(ctx)
    assert bindings["answer"] == 42
    issues = probe.analyze(bindings, ctx)
    assert issues and issues[0].category == "ok"
    assert "42" in issues[0].title
