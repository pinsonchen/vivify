"""Tests for Task #111: goal decomposer awareness of KPI + deployed_features.

Validates:
- Decomposer skips proposals when KPI snapshots show goal already met.
- Decomposer deduplicates against deployed_features.
- Auto-approval of goal-decomposed features (status='approved').
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence
from unittest.mock import MagicMock, patch

import pytest

from vivify.goals.decomposer import (
    AgentGoalDecomposer,
    GoalDecomposerConfig,
    _format_deployed_features,
    _format_recent_snapshots,
)
from vivify.interfaces.goal_decomposer import RepoState
from vivify.models.feature import FeatureRequest, FeatureSpec, Goal, KPI
from vivify.models.snapshot import KpiSnapshot


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def repo_state(tmp_path: Path) -> RepoState:
    return RepoState(repo_root=str(tmp_path), default_branch="main")


@pytest.fixture
def config() -> GoalDecomposerConfig:
    return GoalDecomposerConfig(max_features_per_decompose=3, dedupe_threshold=0.85)


@pytest.fixture
def mock_agent():
    """Agent mock that returns a controllable JSON output."""
    agent = MagicMock()
    agent.heal = MagicMock()
    return agent


@pytest.fixture
def decomposer(mock_agent, tmp_path, config) -> AgentGoalDecomposer:
    # Create fake state.db path so _get_existing_features/_get_kpi_snapshots
    # will find nothing (no table)
    return AgentGoalDecomposer(
        agent=mock_agent,
        repo_root=tmp_path,
        config=config,
    )


def _make_goal(name="Reduce CI flakiness", kpis=None):
    if kpis is None:
        kpis = (
            KPI(name="ci_pass_rate", target=">=98", direction="up"),
        )
    return Goal(name=name, description="Test goal", kpis=kpis)


def _make_snapshot(metrics: dict, source="kpi_monitor") -> KpiSnapshot:
    return KpiSnapshot(
        source=source,
        metrics=metrics,
        captured_at=datetime.now(timezone.utc),
    )


def _make_feature(title: str, status: str = "deployed", fid: int = 1) -> FeatureRequest:
    return FeatureRequest(
        id=fid,
        title=title,
        description="test desc",
        status=status,
    )


def _agent_output(features: list[dict]) -> str:
    """Format agent output as fenced JSON block."""
    payload = json.dumps({"new_features": features}, ensure_ascii=False)
    return f"```json\n{payload}\n```"


# ── Test: KPI snapshots make decomposer skip fully-met goals ────────────────


class TestKpiAwareness:
    def test_all_kpis_met_skips_decomposition(self, decomposer, repo_state, mock_agent):
        """When all KPIs are met, decompose returns [] without calling agent."""
        goal = _make_goal(kpis=(
            KPI(name="ci_pass_rate", target=">=98", direction="up"),
            KPI(name="uptime", target=">=99.9", direction="up"),
        ))
        snapshots = [_make_snapshot({"ci_pass_rate": 99.5, "uptime": 99.95})]

        result = decomposer.decompose(
            goal, repo_state, open_features=[], recent_snapshots=snapshots,
        )

        assert result == []
        mock_agent.heal.assert_not_called()

    def test_partial_kpis_met_limits_features(self, decomposer, repo_state, mock_agent):
        """When >=85% KPIs met, max_features is limited to 1."""
        goal = _make_goal(kpis=(
            KPI(name="a", target=">=90", direction="up"),
            KPI(name="b", target=">=90", direction="up"),
            KPI(name="c", target=">=90", direction="up"),
            KPI(name="d", target=">=90", direction="up"),
            KPI(name="e", target=">=90", direction="up"),
            KPI(name="f", target=">=90", direction="up"),
            KPI(name="g", target=">=90", direction="up"),
        ))
        # 6/7 = 85.7% met
        metrics = {k: 95 for k in "abcdef"}
        metrics["g"] = 50  # not met
        snapshots = [_make_snapshot(metrics)]

        mock_agent.heal.return_value = MagicMock(
            output=_agent_output([
                {"title": "Feature A", "description": "Do A", "type": "feature"},
                {"title": "Feature B", "description": "Do B", "type": "feature"},
            ])
        )

        result = decomposer.decompose(
            goal, repo_state, open_features=[], recent_snapshots=snapshots,
        )

        # Should be limited to 1 feature even though agent returned 2
        assert len(result) <= 1

    def test_no_snapshots_proceeds_normally(self, decomposer, repo_state, mock_agent):
        """With empty snapshots, decomposition proceeds normally."""
        goal = _make_goal()
        mock_agent.heal.return_value = MagicMock(
            output=_agent_output([
                {"title": "Improve CI", "description": "Fix flaky tests", "type": "feature"},
            ])
        )

        result = decomposer.decompose(
            goal, repo_state, open_features=[], recent_snapshots=[],
        )

        assert len(result) == 1
        assert result[0].title == "Improve CI"
        mock_agent.heal.assert_called_once()


# ── Test: deployed_features deduplication ───────────────────────────────────


class TestDeployedFeaturesDedup:
    def test_duplicate_against_deployed_is_filtered(self, decomposer, repo_state, mock_agent):
        """Features matching deployed titles are dropped."""
        goal = _make_goal()
        deployed = [
            _make_feature("Add retry logic for CI", status="deployed", fid=10),
            _make_feature("Fix flaky integration tests", status="verified", fid=11),
        ]

        mock_agent.heal.return_value = MagicMock(
            output=_agent_output([
                # Near-duplicate of deployed feature
                {"title": "Add retry logic for CI jobs", "description": "Retry", "type": "feature"},
                # Novel feature
                {"title": "Parallelize test shards", "description": "Speed up", "type": "feature"},
            ])
        )

        result = decomposer.decompose(
            goal, repo_state, open_features=[], recent_snapshots=[],
            deployed_features=deployed,
        )

        # "Add retry logic for CI jobs" should be deduped against "Add retry logic for CI"
        titles = [s.title for s in result]
        assert "Parallelize test shards" in titles
        assert len(result) == 1

    def test_no_deployed_features_passes_all(self, decomposer, repo_state, mock_agent):
        """When deployed_features is empty, no extra dedup happens."""
        goal = _make_goal()
        mock_agent.heal.return_value = MagicMock(
            output=_agent_output([
                {"title": "Implement parallel test execution", "description": "Desc X", "type": "feature"},
                {"title": "Add build cache layer", "description": "Desc Y", "type": "feature"},
            ])
        )

        result = decomposer.decompose(
            goal, repo_state, open_features=[], recent_snapshots=[],
            deployed_features=[],
        )

        assert len(result) == 2

    def test_dedup_against_both_open_and_deployed(self, decomposer, repo_state, mock_agent):
        """Features matching either open OR deployed lists are dropped."""
        goal = _make_goal()
        open_features = [_make_feature("Optimize CI cache", status="pending", fid=1)]
        deployed = [_make_feature("Add monitoring alerts", status="verified", fid=2)]

        mock_agent.heal.return_value = MagicMock(
            output=_agent_output([
                # Near-dup of open: "Optimize CI cache" vs "Optimize CI cache usage" (ratio ~0.87)
                {"title": "Optimize CI cache usage", "description": "D1", "type": "feature"},
                # Near-dup of deployed: "Add monitoring alerts" vs "Add monitoring alerts setup" (ratio ~0.87)
                {"title": "Add monitoring alerts setup", "description": "D2", "type": "feature"},
                {"title": "Brand new unrelated feature", "description": "D3", "type": "feature"},
            ])
        )

        result = decomposer.decompose(
            goal, repo_state, open_features=open_features, recent_snapshots=[],
            deployed_features=deployed,
        )

        titles = [s.title for s in result]
        assert "Brand new unrelated feature" in titles
        # The other two should be filtered as near-duplicates
        assert len(result) == 1


# ── Test: auto-approval for goal-decomposed features ───────────────────────


class TestAutoApproval:
    def test_store_feature_request_sets_approved_status(self, tmp_path):
        """Goal-decomposed features should be created with status='approved'."""
        from vivify.kernel.loop import Kernel, KernelConfig, KernelDeps

        # Create minimal mocks
        storage = MagicMock()
        storage.create_feature = MagicMock(return_value=42)

        deps = MagicMock(spec=KernelDeps)
        deps.repo_root = tmp_path
        deps.storage = storage
        deps.probes = []

        # We need to test _store_feature_request directly
        # Create a kernel instance with minimal setup (mocked)
        with patch.object(Kernel, "__init__", lambda self, **kw: None):
            kernel = Kernel.__new__(Kernel)
            kernel.deps = deps

        spec = FeatureSpec(
            title="Test feature from goal",
            description="Auto-generated",
            type="feature",
            parent_goal="Reduce CI flakiness",
            priority="P1",
        )

        fid = kernel._store_feature_request(spec)

        assert fid == 42
        # Verify the FeatureRequest was created with status='approved'
        call_args = storage.create_feature.call_args
        created_fr = call_args[0][0]
        assert created_fr.status == "approved"
        assert created_fr.parent_goal == "Reduce CI flakiness"
        assert created_fr.priority == "P1"


# ── Test: format helpers ────────────────────────────────────────────────────


class TestFormatHelpers:
    def test_format_deployed_features_empty(self):
        assert _format_deployed_features([]) == "(none)"

    def test_format_deployed_features_list(self):
        features = [
            _make_feature("Feature A", status="deployed", fid=1),
            _make_feature("Feature B", status="verified", fid=2),
        ]
        result = _format_deployed_features(features)
        assert "- #1 [deployed] Feature A" in result
        assert "- #2 [verified] Feature B" in result

    def test_format_recent_snapshots_empty(self):
        assert _format_recent_snapshots([]) == "(no snapshots)"

    def test_format_recent_snapshots_with_data(self):
        snaps = [_make_snapshot({"cpu": 45, "mem": 70}, source="monitor")]
        result = _format_recent_snapshots(snaps)
        assert "monitor" in result
        assert "cpu=45" in result


# ── Test: storage layer methods ─────────────────────────────────────────────


class TestStorageMethods:
    def test_get_recent_kpi_snapshots(self, tmp_path):
        """SqliteStorageProvider.get_recent_kpi_snapshots returns recent data."""
        from vivify.storage.sqlite_provider import SqliteStorageProvider

        db_path = tmp_path / "state.db"
        provider = SqliteStorageProvider(db_path)
        provider.initialize()

        # Write a snapshot
        snap = KpiSnapshot(
            source="test",
            metrics={"cpu": 50, "mem": 80},
            captured_at=datetime.now(timezone.utc),
        )
        provider.write_snapshot(snap)

        results = provider.get_recent_kpi_snapshots(days=7)
        assert len(results) == 1
        assert results[0].metrics == {"cpu": 50, "mem": 80}

        provider.close()

    def test_get_deployed_features(self, tmp_path):
        """SqliteStorageProvider.get_deployed_features returns recent deployed/verified."""
        from vivify.storage.sqlite_provider import SqliteStorageProvider

        db_path = tmp_path / "state.db"
        provider = SqliteStorageProvider(db_path)
        provider.initialize()

        # Create features in various states
        fr_deployed = FeatureRequest(
            title="Deployed feature",
            description="d",
            status="deployed",
        )
        fr_verified = FeatureRequest(
            title="Verified feature",
            description="d",
            status="verified",
        )
        fr_pending = FeatureRequest(
            title="Pending feature",
            description="d",
            status="pending",
        )
        provider.create_feature(fr_deployed)
        provider.create_feature(fr_verified)
        provider.create_feature(fr_pending)

        results = provider.get_deployed_features(days=30)
        titles = [f.title for f in results]
        assert "Deployed feature" in titles
        assert "Verified feature" in titles
        assert "Pending feature" not in titles

        provider.close()

    def test_get_deployed_features_empty_db(self, tmp_path):
        """get_deployed_features returns [] when no features exist."""
        from vivify.storage.sqlite_provider import SqliteStorageProvider

        db_path = tmp_path / "state.db"
        provider = SqliteStorageProvider(db_path)
        provider.initialize()

        results = provider.get_deployed_features(days=30)
        assert results == []

        provider.close()
