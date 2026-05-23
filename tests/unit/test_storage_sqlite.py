"""Tests for the SQLite storage provider — full CRUD round-trip."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from vivify.models.feature import FeatureRequest
from vivify.models.snapshot import ActionLog, KnowledgeEntry, KpiSnapshot
from vivify.storage.sqlite_provider import SqliteStorageProvider


@pytest.fixture
def storage(tmp_path: Path) -> SqliteStorageProvider:
    sp = SqliteStorageProvider(str(tmp_path / "state.db"))
    sp.initialize()
    yield sp
    sp.close()


def test_feature_crud_roundtrip(storage):
    fid = storage.create_feature(
        FeatureRequest(title="t", description="d", type="feature", priority="P1"),
    )
    assert fid > 0
    fr = storage.get_feature(fid)
    assert fr.title == "t" and fr.priority == "P1" and fr.status == "pending"
    storage.update_feature(fid, status="approved", summary="ok")
    refreshed = storage.get_feature(fid)
    assert refreshed.status == "approved" and refreshed.summary == "ok"

    listed = storage.list_features(status="approved")
    assert any(x.id == fid for x in listed)


def test_action_log_search(storage):
    storage.log_action(ActionLog(run_id="r", round_num=1, action_type="heal",
                                 status="success", category="lint"))
    storage.log_action(ActionLog(run_id="r", round_num=1, action_type="heal",
                                 status="failed", category="ci"))
    rows = storage.search_action_logs(category="ci")
    assert len(rows) == 1 and rows[0].category == "ci"


def test_failure_tracking_and_upgrade(storage):
    h = "abc123"
    assert storage.record_failure(h, "ci", "boom") == 1
    assert storage.record_failure(h, "ci", "boom") == 2
    fid = storage.create_feature(
        FeatureRequest(title="escalated", description="", type="bug", priority="P1"),
    )
    storage.mark_upgraded(h, fid)
    assert storage.get_upgraded_feature_id(h) == fid
    storage.reset_failure(h)
    assert storage.get_failure_count(h) == 0


def test_knowledge(storage):
    storage.add_knowledge(KnowledgeEntry(
        category="bug_fix", pattern="flaky test x",
        solution_summary="rerun with mocks", success=True,
    ))
    rows = storage.search_knowledge(category="bug_fix", pattern="flaky")
    assert any("rerun" in r.solution_summary for r in rows)


def test_kpi_snapshot(storage):
    now = datetime.now(timezone.utc)
    storage.write_snapshot(KpiSnapshot(source="kpi", metrics={"cov": 80.0},
                                       captured_at=now - timedelta(days=2)))
    storage.write_snapshot(KpiSnapshot(source="kpi", metrics={"cov": 70.0},
                                       captured_at=now))
    rows = storage.read_snapshots(since=now - timedelta(days=10))
    assert len(rows) == 2
    assert rows[-1].metrics["cov"] in (80.0, 70.0)
