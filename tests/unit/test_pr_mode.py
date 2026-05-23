"""Tests for ``auto_heal.pr_mode`` — pr_creator + auto_merge."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from auto_heal.pr_mode.auto_merge import AutoMerge, AutoMergeConfig, MergeOutcome
from auto_heal.pr_mode.pr_creator import (
    PrCreator,
    PrCreatorConfig,
    PullRequest,
    _parse_pr_url,
)
from auto_heal.pr_mode.self_grow_guard import DiffClass, GuardDecision
from auto_heal.pr_mode.worktree import Worktree


@pytest.fixture
def worktree(tmp_path) -> Worktree:
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    return Worktree(path=wt_path, branch="auto-heal/fix-1234", base_ref="origin/main")


# ── pr_creator ────────────────────────────────────────────────────────────────
def test_parse_pr_url_extracts_number():
    n, url = _parse_pr_url("https://github.com/foo/bar/pull/42\n")
    assert n == 42
    assert url == "https://github.com/foo/bar/pull/42"


def test_parse_pr_url_no_match_returns_none():
    n, _ = _parse_pr_url("nothing here")
    assert n is None


def test_open_pr_constructs_gh_command(worktree):
    creator = PrCreator(PrCreatorConfig(
        base_branch="main",
        default_labels=("auto-heal",),
    ))
    fake = MagicMock(returncode=0,
                     stdout="https://github.com/foo/bar/pull/7\n", stderr="")
    decision = GuardDecision(classification=DiffClass.PLUGIN,
                             plugin_files=("auto_heal/probes/builtin/x.yml",))
    with patch("auto_heal.pr_mode.pr_creator._run", return_value=fake) as mocked:
        pr = creator.open_pr(worktree, title="T", body="B", decision=decision)

    assert isinstance(pr, PullRequest)
    assert pr.number == 7
    assert pr.url.endswith("/pull/7")
    assert pr.draft is False
    assert "auto-heal" in pr.labels
    assert "auto-heal:plugin-change" in pr.labels

    cmd = mocked.call_args[0][0]
    assert cmd[0:3] == ["gh", "pr", "create"]
    assert "--base" in cmd and cmd[cmd.index("--base") + 1] == "main"
    assert "--head" in cmd and cmd[cmd.index("--head") + 1] == worktree.branch
    assert "--title" in cmd and cmd[cmd.index("--title") + 1] == "T"
    assert "--body-file" in cmd
    label_indices = [i for i, c in enumerate(cmd) if c == "--label"]
    label_values = [cmd[i + 1] for i in label_indices]
    assert "auto-heal" in label_values
    assert "auto-heal:plugin-change" in label_values
    assert "--draft" not in cmd  # plugin → not forced draft


def test_open_pr_kernel_decision_forces_draft(worktree):
    creator = PrCreator(PrCreatorConfig())
    fake = MagicMock(returncode=0, stdout="https://x/y/pull/9", stderr="")
    decision = GuardDecision(classification=DiffClass.KERNEL,
                             kernel_files=("auto_heal/kernel/loop.py",))
    with patch("auto_heal.pr_mode.pr_creator._run", return_value=fake) as mocked:
        pr = creator.open_pr(worktree, title="T", body="B", decision=decision)
    cmd = mocked.call_args[0][0]
    assert "--draft" in cmd
    assert pr.draft is True
    assert "auto-heal:kernel-change" in pr.labels


def test_open_pr_dedupes_labels(worktree):
    creator = PrCreator(PrCreatorConfig(default_labels=("auto-heal", "shared")))
    fake = MagicMock(returncode=0, stdout="https://x/y/pull/3", stderr="")
    with patch("auto_heal.pr_mode.pr_creator._run", return_value=fake) as mocked:
        creator.open_pr(
            worktree, title="t", body="b",
            extra_labels=["shared", "extra"],  # "shared" already in defaults
        )
    cmd = mocked.call_args[0][0]
    label_values = [cmd[i + 1] for i, c in enumerate(cmd) if c == "--label"]
    assert label_values.count("shared") == 1
    assert "extra" in label_values


def test_open_pr_failure_raises(worktree):
    creator = PrCreator()
    fake = MagicMock(returncode=1, stdout="", stderr="boom")
    with patch("auto_heal.pr_mode.pr_creator._run", return_value=fake):
        with pytest.raises(RuntimeError, match="gh pr create failed"):
            creator.open_pr(worktree, title="t", body="b")


def test_push_branch_runs_git_push(worktree):
    creator = PrCreator(PrCreatorConfig(remote="origin"))
    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch("auto_heal.pr_mode.pr_creator._run", return_value=fake) as mocked:
        creator.push_branch(worktree)
    cmd = mocked.call_args[0][0]
    assert cmd == ["git", "push", "-u", "origin", worktree.branch]


def test_push_branch_failure_raises(worktree):
    fake = MagicMock(returncode=1, stdout="", stderr="rejected")
    with patch("auto_heal.pr_mode.pr_creator._run", return_value=fake):
        with pytest.raises(RuntimeError, match="git push failed"):
            PrCreator().push_branch(worktree)


# ── auto_merge ────────────────────────────────────────────────────────────────
def _pr(draft=False, number=42, url="https://github.com/foo/bar/pull/42"):
    return PullRequest(number=number, url=url, branch="b", base="main",
                       draft=draft, labels=("auto-heal",))


def test_auto_merge_skipped_when_disabled():
    am = AutoMerge(AutoMergeConfig(enabled=False))
    out = am.try_merge(_pr())
    assert out.requested is False
    assert "disabled" in (out.skipped_reason or "")


def test_auto_merge_skipped_for_draft_pr():
    am = AutoMerge(AutoMergeConfig(enabled=True))
    out = am.try_merge(_pr(draft=True))
    assert out.requested is False
    assert "draft" in (out.skipped_reason or "")


def test_auto_merge_skipped_when_guard_rejects():
    am = AutoMerge(AutoMergeConfig(enabled=True))
    decision = GuardDecision(classification=DiffClass.KERNEL,
                             kernel_files=("auto_heal/kernel/loop.py",))
    out = am.try_merge(_pr(), decision=decision)
    assert out.requested is False
    assert "guard rejected" in (out.skipped_reason or "")


def test_auto_merge_runs_gh_command():
    am = AutoMerge(AutoMergeConfig(
        enabled=True, method="squash", delete_branch=True, poll_timeout_seconds=0,
    ))
    fake = MagicMock(returncode=0, stdout="", stderr="")
    decision = GuardDecision(classification=DiffClass.PLUGIN,
                             plugin_files=("auto_heal/probes/builtin/x.yml",))
    with patch("auto_heal.pr_mode.auto_merge._run", return_value=fake) as mocked:
        out = am.try_merge(_pr(), decision=decision)
    assert out.requested is True
    cmd = mocked.call_args[0][0]
    assert cmd[:3] == ["gh", "pr", "merge"]
    assert cmd[3] == "42"
    assert "--auto" in cmd
    assert "--squash" in cmd
    assert "--delete-branch" in cmd


def test_auto_merge_handles_gh_failure():
    am = AutoMerge(AutoMergeConfig(enabled=True))
    fake = MagicMock(returncode=1, stdout="", stderr="not mergeable")
    with patch("auto_heal.pr_mode.auto_merge._run", return_value=fake):
        out = am.try_merge(_pr())
    assert out.requested is True
    assert out.merged is False
    assert "not mergeable" in (out.detail or "")


def test_auto_merge_missing_identifier_skipped():
    am = AutoMerge(AutoMergeConfig(enabled=True))
    pr = PullRequest(number=None, url="", branch="b", base="main",
                     draft=False, labels=())
    out = am.try_merge(pr)
    assert out.requested is False
    assert "missing PR identifier" in (out.skipped_reason or "")
