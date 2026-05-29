"""Tests for vivify.knowledge.gc — Knowledge Graph Garbage Collection."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from vivify.knowledge.gc import GCConfig, GCReport, KnowledgeGC, NodeActivity


# ── NodeActivity property tests ─────────────────────────────────────────────


class TestNodeActivity:
    def test_days_since_access_with_last_accessed(self):
        """days_since_access uses last_accessed when available."""
        two_days_ago = (datetime.now() - timedelta(days=2)).isoformat()
        activity = NodeActivity(
            node_id="module:test",
            last_accessed=two_days_ago,
            created_at=datetime.now().isoformat(),
        )
        assert activity.days_since_access == 2

    def test_days_since_access_falls_back_to_created_at(self):
        """days_since_access uses created_at when last_accessed is None."""
        five_days_ago = (datetime.now() - timedelta(days=5)).isoformat()
        activity = NodeActivity(
            node_id="module:test",
            last_accessed=None,
            created_at=five_days_ago,
        )
        assert activity.days_since_access == 5

    def test_staleness_score_fresh(self):
        """Fresh node has staleness near 0."""
        activity = NodeActivity(
            node_id="module:test",
            access_count=5,
            last_accessed=datetime.now().isoformat(),
            created_at=datetime.now().isoformat(),
        )
        assert activity.staleness_score == pytest.approx(0.0, abs=0.05)

    def test_staleness_score_stale(self):
        """90-day old node with >=2 accesses has staleness 1.0."""
        old = (datetime.now() - timedelta(days=90)).isoformat()
        activity = NodeActivity(
            node_id="module:test",
            access_count=3,
            last_accessed=old,
            created_at=old,
        )
        assert activity.staleness_score == 1.0

    def test_staleness_score_accelerated_for_low_access(self):
        """Low access count (< 2) accelerates aging by 1.5x."""
        thirty_days = (datetime.now() - timedelta(days=30)).isoformat()
        low_access = NodeActivity(
            node_id="a",
            access_count=1,
            last_accessed=thirty_days,
            created_at=thirty_days,
        )
        normal_access = NodeActivity(
            node_id="b",
            access_count=5,
            last_accessed=thirty_days,
            created_at=thirty_days,
        )
        # low_access has 1.5x penalty → higher staleness
        assert low_access.staleness_score > normal_access.staleness_score

    def test_to_dict_and_from_dict_roundtrip(self):
        """Serialization roundtrip preserves all fields."""
        now = datetime.now().isoformat()
        original = NodeActivity(
            node_id="module:kernel",
            access_count=7,
            last_accessed=now,
            created_at=now,
            status="stale",
        )
        data = original.to_dict()
        restored = NodeActivity.from_dict(data)
        assert restored.node_id == "module:kernel"
        assert restored.access_count == 7
        assert restored.last_accessed == now
        assert restored.status == "stale"


# ── GCReport tests ──────────────────────────────────────────────────────────


class TestGCReport:
    def test_total_actions(self):
        report = GCReport(
            marked_stale=["a", "b"],
            archived=["c"],
            deleted=["d"],
            evicted=["e", "f"],
        )
        assert report.total_actions == 6

    def test_summary_empty(self):
        report = GCReport()
        assert report.summary == "No actions needed"

    def test_summary_with_actions(self):
        report = GCReport(
            marked_stale=["a"],
            archived=["b", "c"],
        )
        assert "1 marked stale" in report.summary
        assert "2 archived" in report.summary


# ── KnowledgeGC core tests ──────────────────────────────────────────────────


class TestKnowledgeGC:
    @pytest.fixture
    def knowledge_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / ".vivify" / "knowledge"
        d.mkdir(parents=True)
        (d / "modules").mkdir()
        return d

    @pytest.fixture
    def gc(self, knowledge_dir: Path) -> KnowledgeGC:
        config = GCConfig(
            max_nodes=5,
            stale_days=10,
            archive_after_days=20,
            delete_after_days=30,
            min_access_count=2,
            gc_interval_hours=1,
        )
        return KnowledgeGC(config=config, knowledge_dir=knowledge_dir)

    def test_record_access_creates_activity(self, gc: KnowledgeGC):
        gc.record_access("module:kernel")
        assert "module:kernel" in gc.activities
        assert gc.activities["module:kernel"].access_count == 1
        assert gc.activities["module:kernel"].last_accessed is not None

    def test_record_access_increments(self, gc: KnowledgeGC):
        gc.record_access("module:kernel")
        gc.record_access("module:kernel")
        gc.record_access("module:kernel")
        assert gc.activities["module:kernel"].access_count == 3

    def test_record_access_persists(self, gc: KnowledgeGC, knowledge_dir: Path):
        gc.record_access("module:test")
        activity_file = knowledge_dir / "activity.json"
        assert activity_file.exists()
        data = json.loads(activity_file.read_text())
        assert len(data) == 1
        assert data[0]["node_id"] == "module:test"

    def test_load_persisted_activities(self, knowledge_dir: Path):
        """GC loads existing activity.json on init."""
        activity_file = knowledge_dir / "activity.json"
        activity_file.write_text(json.dumps([{
            "node_id": "module:existing",
            "access_count": 5,
            "last_accessed": datetime.now().isoformat(),
            "created_at": datetime.now().isoformat(),
            "status": "active",
        }]))
        config = GCConfig()
        gc = KnowledgeGC(config=config, knowledge_dir=knowledge_dir)
        assert "module:existing" in gc.activities
        assert gc.activities["module:existing"].access_count == 5

    def test_gc_marks_stale(self, gc: KnowledgeGC):
        """Nodes with low access and old age get marked stale."""
        old_date = (datetime.now() - timedelta(days=15)).isoformat()
        gc._activities["module:old"] = NodeActivity(
            node_id="module:old",
            access_count=1,  # below min_access_count=2
            last_accessed=old_date,
            created_at=old_date,
            status="active",
        )
        report = gc.run_gc()
        assert "module:old" in report.marked_stale
        assert gc._activities["module:old"].status == "stale"

    def test_gc_does_not_mark_active_with_enough_access(self, gc: KnowledgeGC):
        """Nodes with sufficient access count are not marked stale even if old."""
        old_date = (datetime.now() - timedelta(days=15)).isoformat()
        gc._activities["module:popular"] = NodeActivity(
            node_id="module:popular",
            access_count=10,  # well above min_access_count
            last_accessed=old_date,
            created_at=old_date,
            status="active",
        )
        report = gc.run_gc()
        assert "module:popular" not in report.marked_stale
        assert gc._activities["module:popular"].status == "active"

    def test_gc_archives_stale(self, gc: KnowledgeGC, knowledge_dir: Path):
        """Stale nodes get archived after archive_after_days."""
        old_date = (datetime.now() - timedelta(days=25)).isoformat()
        gc._activities["module:stale_old"] = NodeActivity(
            node_id="module:stale_old",
            access_count=1,
            last_accessed=old_date,
            created_at=old_date,
            status="stale",
        )
        # Create a module file to be archived
        (knowledge_dir / "modules" / "stale_old.json").write_text("{}")
        report = gc.run_gc()
        assert "module:stale_old" in report.archived
        assert gc._activities["module:stale_old"].status == "archived"
        # Module file should be moved to archive
        assert (knowledge_dir / "archive" / "stale_old.json").exists()
        assert not (knowledge_dir / "modules" / "stale_old.json").exists()

    def test_gc_deletes_archived(self, gc: KnowledgeGC, knowledge_dir: Path):
        """Archived nodes get deleted after delete_after_days."""
        old_date = (datetime.now() - timedelta(days=35)).isoformat()
        gc._activities["module:archived_old"] = NodeActivity(
            node_id="module:archived_old",
            access_count=0,
            last_accessed=old_date,
            created_at=old_date,
            status="archived",
        )
        # Create an archived module file
        archive_dir = knowledge_dir / "archive"
        archive_dir.mkdir(exist_ok=True)
        (archive_dir / "archived_old.json").write_text("{}")
        report = gc.run_gc()
        assert "module:archived_old" in report.deleted
        assert gc._activities["module:archived_old"].status == "deleted"
        assert not (archive_dir / "archived_old.json").exists()

    def test_gc_three_phase_lifecycle(self, gc: KnowledgeGC, knowledge_dir: Path):
        """Full lifecycle: active → stale → archived → deleted."""
        # Phase 1: active → stale (15 days > stale_days=10, < archive_after_days=20)
        date_15d = (datetime.now() - timedelta(days=15)).isoformat()
        (knowledge_dir / "modules" / "lifecycle.json").write_text("{}")
        gc._activities["module:lifecycle"] = NodeActivity(
            node_id="module:lifecycle",
            access_count=0,
            last_accessed=date_15d,
            created_at=date_15d,
            status="active",
        )
        report1 = gc.run_gc()
        assert gc._activities["module:lifecycle"].status == "stale"
        assert "module:lifecycle" in report1.marked_stale

        # Phase 2: stale → archived (advance to 25 days > archive_after_days=20)
        date_25d = (datetime.now() - timedelta(days=25)).isoformat()
        gc._activities["module:lifecycle"].last_accessed = date_25d
        gc._activities["module:lifecycle"].created_at = date_25d
        report2 = gc.run_gc()
        assert gc._activities["module:lifecycle"].status == "archived"
        assert "module:lifecycle" in report2.archived

        # Phase 3: archived → deleted (advance to 35 days > delete_after_days=30)
        date_35d = (datetime.now() - timedelta(days=35)).isoformat()
        gc._activities["module:lifecycle"].last_accessed = date_35d
        gc._activities["module:lifecycle"].created_at = date_35d
        report3 = gc.run_gc()
        assert gc._activities["module:lifecycle"].status == "deleted"
        assert "module:lifecycle" in report3.deleted

    def test_gc_enforces_hard_limit(self, gc: KnowledgeGC):
        """When active nodes exceed max_nodes, excess are evicted."""
        # gc has max_nodes=5, add 7 active nodes with varying staleness
        now = datetime.now()
        for i in range(7):
            days_old = i * 3  # 0, 3, 6, 9, 12, 15, 18 days
            date = (now - timedelta(days=days_old)).isoformat()
            gc._activities[f"module:n{i}"] = NodeActivity(
                node_id=f"module:n{i}",
                access_count=5,  # enough to avoid stale marking
                last_accessed=date,
                created_at=date,
                status="active",
            )
        report = gc.run_gc()
        # Should evict 2 (7 - 5 = 2) most stale nodes
        assert len(report.evicted) == 2
        active_count = sum(
            1 for a in gc._activities.values() if a.status == "active"
        )
        assert active_count == 5

    def test_get_node_weight_unknown(self, gc: KnowledgeGC):
        """Unknown nodes get neutral weight 0.5."""
        assert gc.get_node_weight("module:unknown") == 0.5

    def test_get_node_weight_active_fresh(self, gc: KnowledgeGC):
        """Fresh active nodes get weight close to 1.0."""
        gc._activities["module:fresh"] = NodeActivity(
            node_id="module:fresh",
            access_count=10,
            last_accessed=datetime.now().isoformat(),
            created_at=datetime.now().isoformat(),
            status="active",
        )
        weight = gc.get_node_weight("module:fresh")
        assert weight > 0.9

    def test_get_node_weight_non_active(self, gc: KnowledgeGC):
        """Non-active nodes get minimal weight."""
        gc._activities["module:stale"] = NodeActivity(
            node_id="module:stale",
            access_count=1,
            last_accessed=datetime.now().isoformat(),
            created_at=datetime.now().isoformat(),
            status="stale",
        )
        assert gc.get_node_weight("module:stale") == 0.1

    def test_get_node_weight_decreases_with_staleness(self, gc: KnowledgeGC):
        """Weight decreases as staleness increases."""
        gc._activities["module:mid"] = NodeActivity(
            node_id="module:mid",
            access_count=5,
            last_accessed=(datetime.now() - timedelta(days=45)).isoformat(),
            created_at=(datetime.now() - timedelta(days=90)).isoformat(),
            status="active",
        )
        weight = gc.get_node_weight("module:mid")
        assert 0.1 < weight < 0.9

    def test_get_stats(self, gc: KnowledgeGC):
        """get_stats returns correct counts per status."""
        gc._activities = {
            "a": NodeActivity(node_id="a", status="active"),
            "b": NodeActivity(node_id="b", status="active"),
            "c": NodeActivity(node_id="c", status="stale"),
            "d": NodeActivity(node_id="d", status="archived"),
        }
        stats = gc.get_stats()
        assert stats["active"] == 2
        assert stats["stale"] == 1
        assert stats["archived"] == 1
        assert stats["deleted"] == 0

    def test_node_id_to_module_name(self, gc: KnowledgeGC):
        """Correctly extracts module name from node_id."""
        assert gc._node_id_to_module_name("module:vivify/kernel") == "kernel"
        assert gc._node_id_to_module_name("module:vivify/knowledge") == "knowledge"
        assert gc._node_id_to_module_name("file:vivify/kernel/loop.py") is None

    def test_gc_handles_empty_activities(self, gc: KnowledgeGC):
        """GC with no activities completes without error."""
        report = gc.run_gc()
        assert report.total_actions == 0
        assert report.summary == "No actions needed"

    def test_gc_corrupted_activity_file(self, knowledge_dir: Path):
        """GC handles corrupted activity.json gracefully."""
        activity_file = knowledge_dir / "activity.json"
        activity_file.write_text("not valid json {{{")
        config = GCConfig()
        gc = KnowledgeGC(config=config, knowledge_dir=knowledge_dir)
        assert gc.activities == {}
