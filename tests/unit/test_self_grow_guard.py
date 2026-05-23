"""Tests for ``vivify.pr_mode.self_grow_guard.classify_diff``."""
from __future__ import annotations

from vivify.pr_mode.self_grow_guard import (
    DEFAULT_PLUGIN_PATHS,
    DiffClass,
    GuardDecision,
    classify_diff,
)


def test_plugin_only_diff():
    decision = classify_diff([
        "vivify/probes/builtin/test_coverage.yml",
        "vivify/fixers/builtin/lint_autofix.py",
        "vivify/agents/prompts/templates/fix_issue.md.j2",
        "vivify/agents/prompts/snippets.py",
    ])
    assert decision.classification is DiffClass.PLUGIN
    assert decision.allow_auto_merge is True
    assert decision.force_draft is False
    assert decision.labels == ["vivify:plugin-change"]
    assert decision.kernel_files == ()
    assert len(decision.plugin_files) == 4


def test_kernel_only_diff():
    decision = classify_diff([
        "vivify/kernel/loop.py",
        "vivify/interfaces/probe.py",
    ])
    assert decision.classification is DiffClass.KERNEL
    assert decision.allow_auto_merge is False
    assert decision.force_draft is True
    assert decision.labels == ["vivify:kernel-change"]
    assert decision.plugin_files == ()
    assert len(decision.kernel_files) == 2


def test_mixed_diff_treated_as_kernel_for_safety():
    decision = classify_diff([
        "vivify/probes/builtin/ci_status.yml",
        "vivify/kernel/dispatch.py",
    ])
    assert decision.classification is DiffClass.MIXED
    assert decision.allow_auto_merge is False
    assert decision.force_draft is True
    assert "vivify:kernel-change" in decision.labels
    assert "vivify:plugin-change" in decision.labels


def test_external_only_diff():
    decision = classify_diff([
        "src/server/main.py",
        "tests/test_server.py",
    ])
    assert decision.classification is DiffClass.EXTERNAL
    assert decision.allow_auto_merge is True
    assert decision.force_draft is False
    assert decision.labels == []
    assert decision.kernel_files == ()
    assert decision.plugin_files == ()
    assert len(decision.external_files) == 2


def test_empty_and_whitespace_paths_ignored():
    decision = classify_diff(["", "   ", "\t"])
    assert decision.classification is DiffClass.EXTERNAL
    assert decision.plugin_files == ()
    assert decision.kernel_files == ()
    assert decision.external_files == ()


def test_custom_allowed_plugin_paths():
    decision = classify_diff(
        ["vivify/custom_ext/widget.py", "vivify/kernel/loop.py"],
        allowed_plugin_paths=("vivify/custom_ext/",),
    )
    assert decision.classification is DiffClass.MIXED
    assert "vivify/custom_ext/widget.py" in decision.plugin_files
    assert "vivify/kernel/loop.py" in decision.kernel_files


def test_default_plugin_paths_constant():
    assert "vivify/probes/builtin/" in DEFAULT_PLUGIN_PATHS
    assert "vivify/fixers/builtin/" in DEFAULT_PLUGIN_PATHS
    assert "vivify/agents/prompts/templates/" in DEFAULT_PLUGIN_PATHS
    assert "vivify/agents/prompts/snippets.py" in DEFAULT_PLUGIN_PATHS


def test_snippets_exact_file_match():
    """``snippets.py`` is whitelisted as an exact file, not a directory."""
    decision = classify_diff(["vivify/agents/prompts/snippets.py"])
    assert decision.classification is DiffClass.PLUGIN


def test_guard_decision_is_dataclass():
    decision = GuardDecision(classification=DiffClass.EXTERNAL)
    assert decision.plugin_files == ()
    assert decision.kernel_files == ()
    assert decision.external_files == ()
