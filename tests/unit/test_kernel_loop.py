"""Tests for vivify/kernel/loop.py — Kernel run_once orchestration."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from vivify.kernel.loop import Kernel, KernelConfig, KernelDeps, RoundReport
from vivify.models.issue import Issue, IssueLevel
from vivify.models.feature import FeatureRequest
from vivify.probes.runner import ProbeRunReport


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_deps(tmp_path):
    """Create KernelDeps with all dependencies mocked."""
    deps = MagicMock(spec=KernelDeps)
    deps.repo_root = tmp_path
    deps.storage = MagicMock()
    deps.storage.list_features.return_value = []
    deps.storage.log_action.return_value = 1
    deps.storage.record_failure.return_value = 1
    deps.storage.get_failure_count.return_value = 0
    deps.storage.get_upgraded_feature_id.return_value = None
    deps.agent = MagicMock()
    deps.probes = []
    deps.fixers = MagicMock()
    deps.fixers.get_fixer.return_value = None
    deps.worktrees = MagicMock()
    deps.worktrees.repo_root = tmp_path
    deps.pr_creator = MagicMock()
    deps.auto_merge = None
    deps.health_monitor = None
    return deps


@pytest.fixture
def kernel_config(tmp_path):
    return KernelConfig(
        interval_seconds=1,
        dry_run=False,
        state_dir=str(tmp_path / ".vivify"),
        package_root=None,
        rules=[],
    )


@pytest.fixture
def kernel(mock_deps, kernel_config):
    """Create a Kernel with mocked dependencies."""
    with patch("vivify.kernel.loop.get_deployer", return_value=MagicMock()), \
         patch("vivify.kernel.loop.InstanceLock") as mock_lock, \
         patch("vivify.kernel.loop.DaemonManager"), \
         patch("vivify.kernel.loop.KpiSnapshotVerifier", side_effect=Exception("skip")):
        mock_lock.return_value.acquire.return_value = True
        k = Kernel(deps=mock_deps, config=kernel_config)
    return k


# ── run_once basic ────────────────────────────────────────────────────────────


def test_run_once_returns_report(kernel):
    """run_once returns a RoundReport with incremented round_num."""
    report = kernel.run_once()
    assert isinstance(report, RoundReport)
    assert report.round_num == 1


def test_run_once_increments_round_num(kernel):
    """Successive calls increment the round number."""
    r1 = kernel.run_once()
    r2 = kernel.run_once()
    assert r1.round_num == 1
    assert r2.round_num == 2


def test_run_once_records_duration(kernel):
    """run_once should record non-zero duration."""
    report = kernel.run_once()
    assert report.duration_seconds >= 0


# ── detect stage ──────────────────────────────────────────────────────────────


def test_run_once_calls_probes(kernel, mock_deps):
    """run_once invokes probe runner."""
    with patch("vivify.kernel.loop.run_probes", return_value=[]) as mock_run:
        with patch("vivify.kernel.loop.aggregate_issues", return_value=[]):
            report = kernel.run_once()
    mock_run.assert_called_once()
    assert report.issues_seen == 0


def test_run_once_detects_issues(kernel, mock_deps):
    """Issues from probes are counted."""
    issue = Issue.factory(
        category="test", level=IssueLevel.HIGH,
        title="broken", source_probe="test_probe",
    )
    with patch("vivify.kernel.loop.run_probes", return_value=[]):
        with patch("vivify.kernel.loop.aggregate_issues", return_value=[issue]):
            report = kernel.run_once()
    assert report.issues_seen == 1


# ── probe failure tolerance ───────────────────────────────────────────────────


def test_run_once_survives_probe_exception(kernel):
    """run_once doesn't crash if a probe raises."""
    with patch("vivify.kernel.loop.run_probes", side_effect=Exception("probe boom")):
        report = kernel.run_once()
    # Should not raise; reports an empty round
    assert report.round_num == 1


# ── issue handling ────────────────────────────────────────────────────────────


def test_dry_run_skips_fixes(mock_deps, tmp_path):
    """In dry_run mode, issues are detected but not fixed."""
    config = KernelConfig(
        dry_run=True,
        state_dir=str(tmp_path / ".vivify"),
        package_root=None,
    )
    with patch("vivify.kernel.loop.get_deployer", return_value=MagicMock()), \
         patch("vivify.kernel.loop.InstanceLock") as mock_lock, \
         patch("vivify.kernel.loop.DaemonManager"), \
         patch("vivify.kernel.loop.KpiSnapshotVerifier", side_effect=Exception("skip")):
        mock_lock.return_value.acquire.return_value = True
        k = Kernel(deps=mock_deps, config=config)

    issue = Issue.factory(
        category="test", level=IssueLevel.HIGH,
        title="broken", source_probe="test_probe",
    )
    with patch("vivify.kernel.loop.run_probes", return_value=[]):
        with patch("vivify.kernel.loop.aggregate_issues", return_value=[issue]):
            report = k.run_once()

    assert report.issues_seen == 1
    assert report.direct_fixes == 0
    assert report.agent_fixes == 0


def test_category_filter_skips_other_categories(kernel, mock_deps):
    """only_category filter skips issues of other categories."""
    kernel.config.only_category = "security"
    issue = Issue.factory(
        category="lint", level=IssueLevel.LOW,
        title="style issue", source_probe="lint_probe",
    )
    with patch("vivify.kernel.loop.run_probes", return_value=[]):
        with patch("vivify.kernel.loop.aggregate_issues", return_value=[issue]):
            report = kernel.run_once()
    assert report.issues_skipped == 1


# ── feature pipeline integration ──────────────────────────────────────────────


def test_handle_features_processes_pending(kernel, mock_deps):
    """Pending features are processed through the pipeline."""
    feature = FeatureRequest(
        id=1, title="test feature", description="desc",
        type="feature", status="pending", priority="P1",
    )
    mock_deps.storage.list_features.side_effect = lambda status=None, **kw: (
        [feature] if status in ("pending", "approved") else []
    )

    with patch("vivify.kernel.loop.run_probes", return_value=[]), \
         patch("vivify.kernel.loop.aggregate_issues", return_value=[]), \
         patch("vivify.kernel.loop.FeaturePipeline") as MockPipeline:
        mock_instance = MockPipeline.return_value
        mock_instance.run.return_value = MagicMock(feature_id=1, status="deployed")
        mock_instance._detect_and_recover_timeouts.return_value = None
        mock_instance._recover_failed_deployments.return_value = None
        report = kernel.run_once()

    assert report.features_processed >= 1


def test_handle_features_respects_budget(kernel, mock_deps):
    """Feature processing respects max_features_per_round budget."""
    kernel.config.max_features_per_round = 2
    features = [
        FeatureRequest(id=i, title=f"feat-{i}", description="d",
                       type="feature", status="pending", priority="P2")
        for i in range(5)
    ]
    mock_deps.storage.list_features.side_effect = lambda status=None, **kw: (
        features if status == "pending" else []
    )

    with patch("vivify.kernel.loop.run_probes", return_value=[]), \
         patch("vivify.kernel.loop.aggregate_issues", return_value=[]), \
         patch("vivify.kernel.loop.FeaturePipeline") as MockPipeline:
        mock_instance = MockPipeline.return_value
        mock_instance.run.return_value = MagicMock(feature_id=1, status="deployed")
        mock_instance._detect_and_recover_timeouts.return_value = None
        mock_instance._recover_failed_deployments.return_value = None
        report = kernel.run_once()

    # Should only process up to budget
    assert mock_instance.run.call_count <= 2


def test_handle_features_dry_run_skip(mock_deps, tmp_path):
    """In dry_run mode, features are not processed."""
    config = KernelConfig(
        dry_run=True,
        state_dir=str(tmp_path / ".vivify"),
        package_root=None,
    )
    with patch("vivify.kernel.loop.get_deployer", return_value=MagicMock()), \
         patch("vivify.kernel.loop.InstanceLock") as mock_lock, \
         patch("vivify.kernel.loop.DaemonManager"), \
         patch("vivify.kernel.loop.KpiSnapshotVerifier", side_effect=Exception("skip")):
        mock_lock.return_value.acquire.return_value = True
        k = Kernel(deps=mock_deps, config=config)

    with patch("vivify.kernel.loop.run_probes", return_value=[]), \
         patch("vivify.kernel.loop.aggregate_issues", return_value=[]):
        report = k.run_once()

    assert report.features_processed == 0


# ── code hash ─────────────────────────────────────────────────────────────────


def test_code_hash_empty_when_no_package_root(kernel):
    """Without package_root, code hash should be empty."""
    report = kernel.run_once()
    assert report.code_hash == ""


def test_code_hash_changed_detection(kernel):
    """_code_hash_changed correctly detects changes."""
    kernel._initial_code_hash = "abc123"
    assert kernel._code_hash_changed("abc123") is False
    assert kernel._code_hash_changed("def456") is True
    assert kernel._code_hash_changed("") is False


# ── run_forever stops correctly ───────────────────────────────────────────────


def test_run_forever_max_rounds(kernel):
    """run_forever stops at max_rounds."""
    with patch("vivify.kernel.loop.run_probes", return_value=[]), \
         patch("vivify.kernel.loop.aggregate_issues", return_value=[]):
        kernel.run_forever(max_rounds=2)
    assert kernel._round_num == 2


def test_shutdown_request_stops_loop(kernel):
    """Setting _shutdown_requested stops run_forever."""
    def trigger_shutdown(*args, **kwargs):
        kernel._shutdown_requested = True
        return RoundReport(run_id="x", round_num=1)

    with patch.object(kernel, "run_once", side_effect=trigger_shutdown):
        kernel.run_forever()
    # Should have exited after 1 call
    assert kernel._shutdown_requested is True
