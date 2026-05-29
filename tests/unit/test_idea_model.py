"""Tests for the Idea model and storage layer (Task #115)."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vivify.models.idea import Idea, IDEA_STATUSES
from vivify.storage.sqlite_provider import SqliteStorageProvider


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def storage(tmp_path: Path) -> SqliteStorageProvider:
    """Ephemeral SQLite provider with migrations applied."""
    db = tmp_path / "state.db"
    s = SqliteStorageProvider(db)
    s.initialize()
    return s


# ── Model tests ───────────────────────────────────────────────────────────────


class TestIdeaModel:
    def test_default_values(self):
        idea = Idea()
        assert idea.id is None
        assert idea.title == ""
        assert idea.status == "proposed"
        assert idea.priority == 50
        assert idea.is_active is True
        assert idea.feasibility_score is None
        assert idea.estimated_effort is None
        assert idea.feature_request_ids == []

    def test_is_active_statuses(self):
        for status in ("proposed", "approved", "decomposed"):
            idea = Idea(status=status)
            assert idea.is_active is True

        idea = Idea(status="completed")
        assert idea.is_active is False

    def test_idea_statuses_constant(self):
        assert "proposed" in IDEA_STATUSES
        assert "approved" in IDEA_STATUSES
        assert "decomposed" in IDEA_STATUSES
        assert "completed" in IDEA_STATUSES

    def test_created_at_default(self):
        idea = Idea(title="test")
        assert isinstance(idea.created_at, datetime)
        assert idea.created_at.tzinfo is not None


# ── Storage CRUD tests ────────────────────────────────────────────────────────


class TestIdeaStorage:
    def test_store_and_get(self, storage: SqliteStorageProvider):
        idea = Idea(
            title="Improve login flow",
            description="Redesign the login page for better UX",
            goal_id=1,
            status="proposed",
            priority=80,
            feasibility_score=0.9,
            estimated_effort="medium",
        )
        iid = storage.store_idea(idea)
        assert iid > 0
        assert idea.id == iid

        fetched = storage.get_idea(iid)
        assert fetched is not None
        assert fetched.title == "Improve login flow"
        assert fetched.description == "Redesign the login page for better UX"
        assert fetched.goal_id == 1
        assert fetched.status == "proposed"
        assert fetched.priority == 80
        assert fetched.feasibility_score == 0.9
        assert fetched.estimated_effort == "medium"

    def test_get_nonexistent(self, storage: SqliteStorageProvider):
        result = storage.get_idea(9999)
        assert result is None

    def test_get_ideas_by_status(self, storage: SqliteStorageProvider):
        storage.store_idea(Idea(title="A", status="proposed", priority=30))
        storage.store_idea(Idea(title="B", status="proposed", priority=70))
        storage.store_idea(Idea(title="C", status="approved"))

        proposed = storage.get_ideas_by_status("proposed")
        assert len(proposed) == 2
        # Ordered by priority DESC
        assert proposed[0].title == "B"
        assert proposed[1].title == "A"

        approved = storage.get_ideas_by_status("approved")
        assert len(approved) == 1
        assert approved[0].title == "C"

    def test_get_ideas_by_goal(self, storage: SqliteStorageProvider):
        storage.store_idea(Idea(title="X", goal_id=10))
        storage.store_idea(Idea(title="Y", goal_id=10))
        storage.store_idea(Idea(title="Z", goal_id=20))

        goal10 = storage.get_ideas_by_goal(10)
        assert len(goal10) == 2
        assert {i.title for i in goal10} == {"X", "Y"}

        goal20 = storage.get_ideas_by_goal(20)
        assert len(goal20) == 1

    def test_update_idea_status(self, storage: SqliteStorageProvider):
        iid = storage.store_idea(Idea(title="Test", status="proposed"))
        storage.update_idea_status(iid, "approved")

        idea = storage.get_idea(iid)
        assert idea.status == "approved"
        assert idea.approved_at is not None

    def test_update_idea_status_completed(self, storage: SqliteStorageProvider):
        iid = storage.store_idea(Idea(title="Test", status="decomposed"))
        storage.update_idea_status(iid, "completed")

        idea = storage.get_idea(iid)
        assert idea.status == "completed"
        assert idea.completed_at is not None


# ── Deduplication (find_similar_idea) ─────────────────────────────────────────


class TestFindSimilarIdea:
    def test_exact_match(self, storage: SqliteStorageProvider):
        storage.store_idea(Idea(title="Improve login flow", status="proposed"))
        result = storage.find_similar_idea("Improve login flow")
        assert result is not None
        assert result.title == "Improve login flow"

    def test_substring_match_existing_in_query(self, storage: SqliteStorageProvider):
        storage.store_idea(Idea(title="login flow", status="proposed"))
        result = storage.find_similar_idea("Improve login flow for mobile")
        assert result is not None
        assert result.title == "login flow"

    def test_substring_match_query_in_existing(self, storage: SqliteStorageProvider):
        storage.store_idea(Idea(title="Improve the entire login flow", status="proposed"))
        result = storage.find_similar_idea("login flow")
        assert result is not None

    def test_no_match(self, storage: SqliteStorageProvider):
        storage.store_idea(Idea(title="Improve login flow", status="proposed"))
        result = storage.find_similar_idea("Database optimization")
        assert result is None

    def test_ignores_completed_ideas(self, storage: SqliteStorageProvider):
        storage.store_idea(Idea(title="login flow", status="completed"))
        result = storage.find_similar_idea("login flow")
        assert result is None

    def test_empty_title(self, storage: SqliteStorageProvider):
        result = storage.find_similar_idea("")
        assert result is None

    def test_case_insensitive(self, storage: SqliteStorageProvider):
        storage.store_idea(Idea(title="Login Flow", status="proposed"))
        result = storage.find_similar_idea("login flow")
        assert result is not None


# ── Goal → Idea → FR linkage ──────────────────────────────────────────────────


class TestIdeaFeatureLinkage:
    def test_feature_linked_to_idea(self, storage: SqliteStorageProvider):
        """FRs can be linked to Ideas via idea_id."""
        from vivify.models.feature import FeatureRequest

        iid = storage.store_idea(Idea(title="Auth improvements", status="decomposed"))
        fr = FeatureRequest(
            title="Add OAuth2 support",
            description="Implement OAuth2 login",
            idea_id=iid,
        )
        fid = storage.create_feature(fr)

        fetched_fr = storage.get_feature(fid)
        assert fetched_fr.idea_id == iid

    def test_idea_auto_complete_logic(self, storage: SqliteStorageProvider):
        """When all FRs under an Idea reach terminal state, Idea can be completed."""
        from vivify.models.feature import FeatureRequest

        iid = storage.store_idea(Idea(title="Perf boost", status="decomposed"))

        # Create 2 FRs under this idea
        fr1 = FeatureRequest(title="Cache layer", description="Add cache", idea_id=iid)
        fr2 = FeatureRequest(title="Query opt", description="Optimize queries", idea_id=iid)
        fid1 = storage.create_feature(fr1)
        fid2 = storage.create_feature(fr2)

        # Both FRs are verified → idea should be completable
        storage.update_feature(fid1, status="verified")
        storage.update_feature(fid2, status="deployed")

        # Check: all FRs under this idea are in terminal states
        all_frs = storage.list_features()
        idea_frs = [f for f in all_frs if f.idea_id == iid]
        terminal = ("verified", "deployed", "rejected")
        assert all(f.status in terminal for f in idea_frs)

        # Now mark idea as completed
        storage.update_idea_status(iid, "completed")
        idea = storage.get_idea(iid)
        assert idea.status == "completed"


# ── Migration test ────────────────────────────────────────────────────────────


class TestMigration:
    def test_ideas_table_created(self, storage: SqliteStorageProvider):
        """Verify that the ideas table exists after migration."""
        with storage._guarded() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ideas'"
            ).fetchone()
        assert row is not None

    def test_schema_version_recorded(self, storage: SqliteStorageProvider):
        """Migration version 6 should be recorded."""
        with storage._guarded() as conn:
            row = conn.execute(
                "SELECT version FROM _schema_migrations WHERE version = 6"
            ).fetchone()
        assert row is not None
