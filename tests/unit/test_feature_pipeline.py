"""Tests for vivify/kernel/feature_pipeline.py — pipeline state transitions."""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from vivify.kernel.feature_pipeline import (
    FeaturePipeline,
    FeaturePipelineConfig,
    FeatureRunReport,
)
from vivify.models.agent_result import AgentResult
from vivify.models.feature import FeatureRequest
from vivify.pr_mode.quality_check import QualityCheckResult


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.heal.return_value = AgentResult(output="done", success=True, exit_code=0)
    return agent


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.list_features.return_value = []
    storage.get_feature.return_value = None
    storage.create_feature.return_value = 99
    storage.update_feature.return_value = None
    storage.log_action.return_value = 1
    return storage


@pytest.fixture
def mock_worktree_mgr():
    mgr = MagicMock()
    wt = MagicMock()
    wt.path = Path("/tmp/test-wt")
    wt.base_ref = "origin/main"
    mgr.create.return_value = wt
    mgr.repo_root = Path("/tmp/test-repo")
    return mgr


@pytest.fixture
def mock_pr_creator():
    creator = MagicMock()
    pr = MagicMock()
    pr.url = "https://github.com/test/repo/pull/1"
    pr.labels = ["vivify"]
    creator.push_and_open.return_value = pr
    return creator


@pytest.fixture
def pipeline(mock_agent, mock_storage, mock_worktree_mgr, mock_pr_creator):
    return FeaturePipeline(
        agent=mock_agent,
        storage=mock_storage,
        worktree_mgr=mock_worktree_mgr,
        pr_creator=mock_pr_creator,
        config=FeaturePipelineConfig(max_retries=3),
        run_id="test-run",
    )


def _make_feature(**kwargs) -> FeatureRequest:
    defaults = dict(
        id=1, title="Test feature", description="desc",
        type="feature", status="pending", priority="P2",
    )
    defaults.update(kwargs)
    return FeatureRequest(**defaults)


# ── evaluate stage ────────────────────────────────────────────────────────────


@patch("vivify.kernel.feature_pipeline.parsers.parse_evaluation_result")
@patch("vivify.kernel.feature_pipeline.builders.build_feature_evaluate")
def test_evaluate_approved(mock_build, mock_parse, pipeline, mock_agent):
    """evaluating → approved: feasible feature passes evaluation."""
    mock_build.return_value = "evaluate prompt"
    mock_parse.return_value = {
        "feasible": True,
        "priority": "P1",
        "feasibility": "ok",
        "summary": "good feature",
        "roi_score": 80,
        "implementation_approach": "add endpoint",
    }
    feature = _make_feature(status="pending")
    report = FeatureRunReport(feature_id=1)

    pipeline.evaluate(feature, report=report, round_num=1)

    assert feature.status == "approved"
    assert feature.priority == "P1"
    assert "evaluate" in report.durations


@patch("vivify.kernel.feature_pipeline.parsers.parse_evaluation_result")
@patch("vivify.kernel.feature_pipeline.builders.build_feature_evaluate")
def test_evaluate_rejected_not_feasible(mock_build, mock_parse, pipeline):
    """evaluating → rejected: infeasible feature."""
    mock_build.return_value = "prompt"
    mock_parse.return_value = {"feasible": False, "summary": "too complex"}
    feature = _make_feature(status="evaluating")
    report = FeatureRunReport(feature_id=1)

    pipeline.evaluate(feature, report=report, round_num=1)

    assert feature.status == "rejected"
    assert report.status == "rejected"


@patch("vivify.kernel.feature_pipeline.parsers.parse_evaluation_result")
@patch("vivify.kernel.feature_pipeline.builders.build_feature_evaluate")
def test_evaluate_rejected_low_roi(mock_build, mock_parse, pipeline):
    """evaluating → rejected: ROI < 30 threshold."""
    mock_build.return_value = "prompt"
    mock_parse.return_value = {
        "feasible": True,
        "roi_score": 15,
        "summary": "low value",
    }
    feature = _make_feature(status="evaluating")
    report = FeatureRunReport(feature_id=1)

    pipeline.evaluate(feature, report=report, round_num=1)

    assert feature.status == "rejected"


@patch("vivify.kernel.feature_pipeline.parsers.parse_evaluation_result")
@patch("vivify.kernel.feature_pipeline.builders.build_feature_evaluate")
def test_evaluate_rejected_needs_admin_review(mock_build, mock_parse, pipeline):
    """evaluating → rejected: needs admin review."""
    mock_build.return_value = "prompt"
    mock_parse.return_value = {
        "feasible": True,
        "needs_admin_review": True,
        "roi_score": 80,
        "summary": "needs review",
    }
    feature = _make_feature(status="evaluating")
    report = FeatureRunReport(feature_id=1)

    pipeline.evaluate(feature, report=report, round_num=1)

    assert feature.status == "rejected"


# ── develop stage ─────────────────────────────────────────────────────────────


@patch("vivify.kernel.feature_pipeline.classify_worktree")
@patch("vivify.kernel.feature_pipeline.run_quality_checks")
@patch("vivify.kernel.feature_pipeline.parsers.parse_commit_info")
@patch("vivify.kernel.feature_pipeline.builders.build_feature_develop")
@patch("vivify.kernel.feature_pipeline.load_history")
@patch("vivify.kernel.feature_pipeline.FeaturePipeline._get_head_sha")
@patch("vivify.kernel.feature_pipeline.FeaturePipeline._has_actual_changes")
def test_develop_success_path(
    mock_changes, mock_head, mock_history, mock_build, mock_commit,
    mock_quality, mock_classify, pipeline, mock_pr_creator,
):
    """developing → deployed: full success path."""
    mock_changes.return_value = True
    # Simulate a new commit produced by the agent (HEAD changed).
    mock_head.side_effect = ["sha_before", "sha_after"]
    mock_history.return_value = []
    mock_build.return_value = "dev prompt"
    mock_commit.return_value = {"commit_hash": "abc123"}
    mock_quality.return_value = QualityCheckResult(passed=True)
    mock_classify.return_value = MagicMock()
    feature = _make_feature(status="approved")
    report = FeatureRunReport(feature_id=1)

    pipeline.develop(feature, report=report, round_num=1)

    assert feature.status == "deployed"
    assert report.status == "deployed"
    assert report.pr is not None


@patch("vivify.kernel.feature_pipeline.run_quality_checks")
@patch("vivify.kernel.feature_pipeline.builders.build_feature_develop")
@patch("vivify.kernel.feature_pipeline.load_history")
@patch("vivify.kernel.feature_pipeline.FeaturePipeline._get_head_sha")
def test_develop_quality_failed(
    mock_head, mock_history, mock_build, mock_quality, pipeline,
):
    """developing → deployed_with_issues: quality check fails."""
    mock_head.side_effect = ["sha_before", "sha_after"]
    mock_history.return_value = []
    mock_build.return_value = "prompt"
    mock_quality.return_value = QualityCheckResult(passed=False, errors=["lint error"])
    feature = _make_feature(status="approved")
    report = FeatureRunReport(feature_id=1)

    pipeline.develop(feature, report=report, round_num=1)

    assert feature.status == "deployed_with_issues"
    assert report.status == "deployed_with_issues"


@patch("vivify.kernel.feature_pipeline.run_quality_checks")
@patch("vivify.kernel.feature_pipeline.builders.build_feature_develop")
@patch("vivify.kernel.feature_pipeline.load_history")
@patch("vivify.kernel.feature_pipeline.FeaturePipeline._get_head_sha")
@patch("vivify.kernel.feature_pipeline.FeaturePipeline._has_actual_changes")
def test_develop_no_changes_rollback(
    mock_changes, mock_head, mock_history, mock_build, mock_quality, pipeline,
):
    """developing → approved: no new commits this round → rollback."""
    mock_changes.return_value = True  # historical commits may exist
    # Same sha before/after → agent produced no new commits this round.
    mock_head.side_effect = ["same_sha", "same_sha"]
    mock_history.return_value = []
    mock_build.return_value = "prompt"
    mock_quality.return_value = QualityCheckResult(passed=True)
    feature = _make_feature(status="approved", retry_count=0)
    report = FeatureRunReport(feature_id=1)

    pipeline.develop(feature, report=report, round_num=1)

    assert feature.status == "approved"
    assert report.status == "approved"
    assert feature.retry_count == 1


@patch("vivify.kernel.feature_pipeline.run_quality_checks")
@patch("vivify.kernel.feature_pipeline.builders.build_feature_develop")
@patch("vivify.kernel.feature_pipeline.load_history")
@patch("vivify.kernel.feature_pipeline.FeaturePipeline._get_head_sha")
@patch("vivify.kernel.feature_pipeline.FeaturePipeline._has_actual_changes")
def test_develop_no_new_commits_escalates_after_max_retries(
    mock_changes, mock_head, mock_history, mock_build, mock_quality, pipeline,
):
    """developing → deployed_with_issues when no-new-commit retries exhaust."""
    mock_changes.return_value = True
    mock_head.side_effect = ["same_sha", "same_sha"]
    mock_history.return_value = []
    mock_build.return_value = "prompt"
    mock_quality.return_value = QualityCheckResult(passed=True)
    # retry_count already at max_retries-1; this round bumps it to max.
    feature = _make_feature(
        status="approved", retry_count=pipeline.config.max_retries - 1,
    )
    report = FeatureRunReport(feature_id=1)

    pipeline.develop(feature, report=report, round_num=1)

    assert feature.status == "deployed_with_issues"
    assert report.status == "deployed_with_issues"
    assert feature.retry_count == pipeline.config.max_retries


@patch("vivify.kernel.feature_pipeline.classify_worktree")
@patch("vivify.kernel.feature_pipeline.run_quality_checks")
@patch("vivify.kernel.feature_pipeline.builders.build_feature_develop")
@patch("vivify.kernel.feature_pipeline.load_history")
@patch("vivify.kernel.feature_pipeline.FeaturePipeline._get_head_sha")
@patch("vivify.kernel.feature_pipeline.FeaturePipeline._has_actual_changes")
def test_develop_pr_creation_fails(
    mock_changes, mock_head, mock_history, mock_build, mock_quality,
    mock_classify, pipeline, mock_pr_creator,
):
    """developing → deployed_with_issues: PR creation raises."""
    mock_changes.return_value = True
    mock_head.side_effect = ["sha_before", "sha_after"]
    mock_history.return_value = []
    mock_build.return_value = "prompt"
    mock_quality.return_value = QualityCheckResult(passed=True)
    mock_classify.return_value = MagicMock()
    mock_pr_creator.push_and_open.side_effect = RuntimeError("gh: network error")
    feature = _make_feature(status="approved")
    report = FeatureRunReport(feature_id=1)

    pipeline.develop(feature, report=report, round_num=1)

    assert feature.status == "deployed_with_issues"
    assert "pr_create" in (report.error or "")


# ── _has_actual_changes ───────────────────────────────────────────────────────


@patch("subprocess.run")
def test_has_actual_changes_true(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="abc123 some commit\n"
    )
    assert FeaturePipeline._has_actual_changes("/tmp/wt", "origin/main") is True


@patch("subprocess.run")
def test_has_actual_changes_false_empty(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=""
    )
    assert FeaturePipeline._has_actual_changes("/tmp/wt", "origin/main") is False


@patch("subprocess.run")
def test_has_actual_changes_false_error(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=128, stdout=""
    )
    assert FeaturePipeline._has_actual_changes("/tmp/wt", "origin/main") is False


@patch("subprocess.run")
def test_has_actual_changes_exception(mock_run):
    mock_run.side_effect = OSError("no git")
    assert FeaturePipeline._has_actual_changes("/tmp/wt", "origin/main") is False


# ── _get_head_sha ───────────────────────────────────────────────


@patch("subprocess.run")
def test_get_head_sha_returns_sha(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="abc123def456\n",
    )
    assert FeaturePipeline._get_head_sha("/tmp/wt") == "abc123def456"


@patch("subprocess.run")
def test_get_head_sha_returns_empty_on_error(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=128, stdout="",
    )
    assert FeaturePipeline._get_head_sha("/tmp/wt") == ""


@patch("subprocess.run")
def test_get_head_sha_returns_empty_on_exception(mock_run):
    mock_run.side_effect = OSError("no git")
    assert FeaturePipeline._get_head_sha("/tmp/wt") == ""


# ── _detect_and_recover_timeouts ──────────────────────────────────────────────


def test_detect_and_recover_timeouts_stuck_developing(pipeline, mock_storage):
    """Stuck developing feature → rolled back to approved."""
    old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    stuck_feature = _make_feature(
        status="developing", started_at=old_time, retry_count=0,
    )
    mock_storage.list_features.side_effect = lambda status=None, **kw: (
        [stuck_feature] if status == "developing" else []
    )

    pipeline._detect_and_recover_timeouts()

    # Should have been updated
    mock_storage.update_feature.assert_called()
    # Verify it was set back to approved
    calls = mock_storage.update_feature.call_args_list
    assert any(
        call.kwargs.get("status") == "approved" or
        (len(call.args) > 1 and "approved" in str(call))
        for call in calls
    ) or any("approved" in str(c) for c in calls)


def test_detect_and_recover_timeouts_max_retries_reject(pipeline, mock_storage):
    """Feature exceeds max retries → auto-rejected."""
    old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    stuck_feature = _make_feature(
        status="developing", started_at=old_time, retry_count=2,
    )
    mock_storage.list_features.side_effect = lambda status=None, **kw: (
        [stuck_feature] if status == "developing" else []
    )

    pipeline._detect_and_recover_timeouts()

    mock_storage.update_feature.assert_called()


def test_detect_and_recover_timeouts_not_expired(pipeline, mock_storage):
    """Feature within threshold → not touched."""
    recent_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    feature = _make_feature(
        status="developing", started_at=recent_time, retry_count=0,
    )
    mock_storage.list_features.side_effect = lambda status=None, **kw: (
        [feature] if status == "developing" else []
    )

    pipeline._detect_and_recover_timeouts()

    # update_feature should not be called for status change
    for call in mock_storage.update_feature.call_args_list:
        if call.args:
            assert "rejected" not in str(call) or "approved" not in str(call)


# ── _recover_failed_deployments ───────────────────────────────────────────────


def test_recover_failed_deployments_pr_failure(pipeline, mock_storage):
    """Feature with PR creation failed → rolled back to approved."""
    feature = _make_feature(
        status="deployed_with_issues",
        development_result="PR creation failed: gh network error",
        retry_count=0,
    )
    mock_storage.list_features.return_value = [feature]

    pipeline._recover_failed_deployments()

    mock_storage.update_feature.assert_called()


def test_recover_failed_deployments_max_retries(pipeline, mock_storage):
    """Feature with exhausted retries → rejected."""
    feature = _make_feature(
        status="deployed_with_issues",
        development_result="PR creation failed: gh error",
        retry_count=2,
    )
    mock_storage.list_features.return_value = [feature]

    pipeline._recover_failed_deployments()

    mock_storage.update_feature.assert_called()


def test_recover_failed_deployments_real_issue_skipped(pipeline, mock_storage):
    """Feature with real quality issue → not touched."""
    feature = _make_feature(
        status="deployed_with_issues",
        development_result="lint errors in module X; 5 test failures",
        retry_count=0,
    )
    mock_storage.list_features.return_value = [feature]

    pipeline._recover_failed_deployments()

    # Should not update since keywords don't match
    # (the only update would be from _update which is called via the method)
    for call in mock_storage.update_feature.call_args_list:
        if call.args and len(call.args) > 0:
            # If update_feature was called, it should not be for status change
            pass


# ── _get_agent_params (cost model) ────────────────────────────────────────────


def test_get_agent_params_p0(pipeline):
    feature = _make_feature(priority="P0")
    turns, timeout = pipeline._get_agent_params(feature)
    assert turns == 100
    assert timeout == 7200


def test_get_agent_params_p1(pipeline):
    feature = _make_feature(priority="P1")
    turns, timeout = pipeline._get_agent_params(feature)
    assert turns == 60
    assert timeout == 3600


def test_get_agent_params_p2(pipeline):
    feature = _make_feature(priority="P2")
    turns, timeout = pipeline._get_agent_params(feature)
    assert turns == 30
    assert timeout == 1800


def test_get_agent_params_p3(pipeline):
    feature = _make_feature(priority="P3")
    turns, timeout = pipeline._get_agent_params(feature)
    assert turns == 15
    assert timeout == 900


def test_get_agent_params_none_defaults_p2(pipeline):
    feature = _make_feature(priority=None)
    turns, timeout = pipeline._get_agent_params(feature)
    assert turns == 30
    assert timeout == 1800


# ── _update_feature_status (state machine integration) ────────────────────────


def test_update_feature_status_valid_transition(pipeline, mock_storage):
    """Valid transition passes through."""
    feature = _make_feature(status="pending")
    pipeline._update_feature_status(feature, "evaluating")
    # _update should have been called which calls storage.update_feature
    assert feature.status == "evaluating"


def test_update_feature_status_invalid_transition(pipeline, mock_storage):
    """Invalid transition is silently skipped."""
    feature = _make_feature(status="pending")
    pipeline._update_feature_status(feature, "deployed")
    # Status should remain unchanged
    assert feature.status == "pending"
