"""Extended tests for vivify/storage/sqlite_provider.py — concurrency, edge cases."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vivify.models.feature import FeatureRequest
from vivify.models.snapshot import ActionLog, KnowledgeEntry, KpiSnapshot
from vivify.storage.sqlite_provider import SqliteStorageProvider


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def storage(tmp_path: Path) -> SqliteStorageProvider:
    sp = SqliteStorageProvider(str(tmp_path / "test.db"))
    sp.initialize()
    yield sp
    sp.close()


@pytest.fixture
def memory_storage() -> SqliteStorageProvider:
    """In-memory SQLite for fast tests."""
    sp = SqliteStorageProvider(":memory:")
    sp.initialize()
    yield sp
    sp.close()


# ── Feature CRUD ──────────────────────────────────────────────────────────────


def test_create_feature_returns_incrementing_ids(memory_storage):
    f1 = memory_storage.create_feature(
        FeatureRequest(title="a", description="d1", type="feature")
    )
    f2 = memory_storage.create_feature(
        FeatureRequest(title="b", description="d2", type="bug")
    )
    assert f2 > f1


def test_get_feature_nonexistent_returns_none(memory_storage):
    assert memory_storage.get_feature(9999) is None


def test_update_feature_multiple_fields(memory_storage):
    fid = memory_storage.create_feature(
        FeatureRequest(title="x", description="y", type="feature", priority="P3")
    )
    memory_storage.update_feature(
        fid, status="approved", priority="P0", summary="upgraded"
    )
    fr = memory_storage.get_feature(fid)
    assert fr.status == "approved"
    assert fr.priority == "P0"
    assert fr.summary == "upgraded"


def test_update_feature_invalid_column_raises(memory_storage):
    fid = memory_storage.create_feature(
        FeatureRequest(title="x", description="y", type="feature")
    )
    with pytest.raises(ValueError, match="not allowed"):
        memory_storage.update_feature(fid, invalid_col="bad")


def test_update_feature_empty_fields_noop(memory_storage):
    fid = memory_storage.create_feature(
        FeatureRequest(title="x", description="y", type="feature")
    )
    # Should not raise
    memory_storage.update_feature(fid)


def test_list_features_by_status(memory_storage):
    memory_storage.create_feature(
        FeatureRequest(title="a", description="", type="feature", status="pending")
    )
    memory_storage.create_feature(
        FeatureRequest(title="b", description="", type="feature", status="approved")
    )
    memory_storage.create_feature(
        FeatureRequest(title="c", description="", type="bug", status="pending")
    )
    pending = memory_storage.list_features(status="pending")
    assert len(pending) == 2
    assert all(f.status == "pending" for f in pending)

    approved = memory_storage.list_features(status="approved")
    assert len(approved) == 1


def test_list_features_limit(memory_storage):
    for i in range(10):
        memory_storage.create_feature(
            FeatureRequest(title=f"f{i}", description="", type="feature")
        )
    limited = memory_storage.list_features(limit=3)
    assert len(limited) == 3


def test_list_features_all(memory_storage):
    for i in range(5):
        memory_storage.create_feature(
            FeatureRequest(title=f"f{i}", description="", type="feature")
        )
    all_features = memory_storage.list_features()
    assert len(all_features) == 5


def test_feature_roundtrip_preserves_fields(memory_storage):
    """All fields survive create → get roundtrip."""
    fr = FeatureRequest(
        title="full test",
        description="desc",
        type="bug",
        parent_goal="G1",
        priority="P1",
        status="approved",
        feasibility="feasible",
        summary="sum",
        verification_method="run tests",
        retry_count=2,
    )
    fid = memory_storage.create_feature(fr)
    got = memory_storage.get_feature(fid)
    assert got.title == "full test"
    assert got.type == "bug"
    assert got.parent_goal == "G1"
    assert got.priority == "P1"
    assert got.status == "approved"
    assert got.feasibility == "feasible"
    assert got.summary == "sum"
    assert got.verification_method == "run tests"
    assert got.retry_count == 2


# ── Action logs ───────────────────────────────────────────────────────────────


def test_action_log_roundtrip(memory_storage):
    lid = memory_storage.log_action(
        ActionLog(
            run_id="run-1", round_num=3, action_type="heal",
            status="success", category="ci", title="fix build",
            result_summary="patched", duration_seconds=12.5,
            details={"key": "val"},
        )
    )
    assert lid > 0
    logs = memory_storage.search_action_logs(category="ci")
    assert len(logs) == 1
    assert logs[0].action_type == "heal"
    assert logs[0].details == {"key": "val"}
    assert logs[0].duration_seconds == 12.5


def test_action_log_search_limit(memory_storage):
    for i in range(20):
        memory_storage.log_action(
            ActionLog(run_id="r", round_num=i, action_type="detect",
                      category="perf")
        )
    logs = memory_storage.search_action_logs(category="perf", limit=5)
    assert len(logs) == 5


# ── Failure tracking ──────────────────────────────────────────────────────────


def test_failure_tracking_increment(memory_storage):
    assert memory_storage.record_failure("h1", "cat", "title") == 1
    assert memory_storage.record_failure("h1", "cat", "title") == 2
    assert memory_storage.record_failure("h1", "cat", "title") == 3
    assert memory_storage.get_failure_count("h1") == 3


def test_failure_tracking_reset(memory_storage):
    memory_storage.record_failure("h2", "cat", "title")
    memory_storage.record_failure("h2", "cat", "title")
    memory_storage.reset_failure("h2")
    assert memory_storage.get_failure_count("h2") == 0


def test_failure_tracking_upgrade(memory_storage):
    fid = memory_storage.create_feature(
        FeatureRequest(title="esc", description="", type="bug")
    )
    memory_storage.record_failure("h3", "cat", "title")
    memory_storage.mark_upgraded("h3", fid)
    assert memory_storage.get_upgraded_feature_id("h3") == fid


def test_failure_nonexistent_hash(memory_storage):
    assert memory_storage.get_failure_count("does_not_exist") == 0
    assert memory_storage.get_upgraded_feature_id("nope") is None


# ── Knowledge ─────────────────────────────────────────────────────────────────


def test_knowledge_search_pattern(memory_storage):
    memory_storage.add_knowledge(
        KnowledgeEntry(category="feature", pattern="add pagination",
                       solution_summary="use offset/limit", success=True)
    )
    memory_storage.add_knowledge(
        KnowledgeEntry(category="feature", pattern="add caching",
                       solution_summary="redis layer", success=True)
    )
    results = memory_storage.search_knowledge("feature", "paginat")
    assert len(results) == 1
    assert "offset" in results[0].solution_summary


def test_knowledge_different_category_no_cross(memory_storage):
    memory_storage.add_knowledge(
        KnowledgeEntry(category="bug_fix", pattern="null check",
                       solution_summary="guard clause", success=True)
    )
    results = memory_storage.search_knowledge("feature", "null")
    assert len(results) == 0


# ── KPI snapshots ─────────────────────────────────────────────────────────────


def test_kpi_snapshot_write_read(memory_storage):
    now = datetime.now(timezone.utc)
    memory_storage.write_snapshot(
        KpiSnapshot(source="test", metrics={"uptime": 99.5},
                    overall_score=85.0, grade="B", captured_at=now)
    )
    snaps = memory_storage.read_snapshots(since=now - timedelta(hours=1))
    assert len(snaps) == 1
    assert snaps[0].source == "test"
    assert snaps[0].metrics["uptime"] == 99.5
    assert snaps[0].overall_score == 85.0


def test_kpi_snapshot_since_filter(memory_storage):
    now = datetime.now(timezone.utc)
    memory_storage.write_snapshot(
        KpiSnapshot(source="old", metrics={},
                    captured_at=now - timedelta(days=10))
    )
    memory_storage.write_snapshot(
        KpiSnapshot(source="new", metrics={},
                    captured_at=now)
    )
    # Only recent
    snaps = memory_storage.read_snapshots(since=now - timedelta(days=1))
    assert len(snaps) == 1
    assert snaps[0].source == "new"


# ── Concurrent access ─────────────────────────────────────────────────────────


def test_concurrent_writes(storage):
    """Multiple threads can write features concurrently without crashing."""
    errors: list[Exception] = []

    def writer(tid):
        try:
            for i in range(10):
                storage.create_feature(
                    FeatureRequest(
                        title=f"thread-{tid}-{i}",
                        description="concurrent",
                        type="feature",
                    )
                )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent write errors: {errors}"
    all_features = storage.list_features()
    assert len(all_features) == 40  # 4 threads × 10 features


def test_concurrent_read_write(storage):
    """Reads and writes can happen concurrently."""
    # Seed some data
    for i in range(5):
        storage.create_feature(
            FeatureRequest(title=f"seed-{i}", description="", type="feature")
        )

    errors: list[Exception] = []
    results: list[int] = []

    def reader():
        try:
            for _ in range(20):
                features = storage.list_features()
                results.append(len(features))
        except Exception as e:
            errors.append(e)

    def writer():
        try:
            for i in range(10):
                storage.create_feature(
                    FeatureRequest(title=f"new-{i}", description="", type="bug")
                )
        except Exception as e:
            errors.append(e)

    t_read = threading.Thread(target=reader)
    t_write = threading.Thread(target=writer)
    t_read.start()
    t_write.start()
    t_read.join()
    t_write.join()

    assert not errors
    # Final count should be 15 (5 seed + 10 new)
    final = storage.list_features()
    assert len(final) == 15


# ── initialize idempotent ─────────────────────────────────────────────────────


def test_initialize_idempotent(tmp_path):
    """Calling initialize() twice doesn't crash or corrupt."""
    sp = SqliteStorageProvider(str(tmp_path / "idem.db"))
    sp.initialize()
    sp.initialize()  # second call should be safe
    fid = sp.create_feature(
        FeatureRequest(title="after reinit", description="", type="feature")
    )
    assert fid > 0
    sp.close()


def test_close_and_reopen(tmp_path):
    """Data persists after close and reopen."""
    db_path = str(tmp_path / "persist.db")
    sp = SqliteStorageProvider(db_path)
    sp.initialize()
    fid = sp.create_feature(
        FeatureRequest(title="persist", description="data", type="feature")
    )
    sp.close()

    sp2 = SqliteStorageProvider(db_path)
    sp2.initialize()
    fr = sp2.get_feature(fid)
    assert fr is not None
    assert fr.title == "persist"
    sp2.close()
