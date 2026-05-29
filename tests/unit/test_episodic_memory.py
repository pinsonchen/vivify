"""Unit tests for L2 Episodic Memory."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from vivify.intelligence.episodic_memory import Episode, EpisodicMemory


# ── Episode dataclass tests ─────────────────────────────────────────────────


class TestEpisode:
    def _make_episode(self, hours_ago: float = 0, **kwargs) -> Episode:
        ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        defaults = {
            "action_id": "1",
            "probe_id": "lint_typecheck",
            "issue_category": "lint",
            "issue_summary": "Type error in module X",
            "fix_summary": "Fixed type annotation",
            "files_changed": ["src/module.py"],
            "timestamp": ts,
            "success": True,
        }
        defaults.update(kwargs)
        return Episode(**defaults)

    def test_age_hours_recent(self):
        ep = self._make_episode(hours_ago=2)
        assert 1.9 <= ep.age_hours <= 2.1

    def test_age_hours_old(self):
        ep = self._make_episode(hours_ago=48)
        assert 47.9 <= ep.age_hours <= 48.1

    def test_recency_weight_within_24h(self):
        ep = self._make_episode(hours_ago=12)
        assert ep.recency_weight == 1.0

    def test_recency_weight_decays_after_24h(self):
        ep = self._make_episode(hours_ago=84)  # 3.5 days
        weight = ep.recency_weight
        assert 0.1 < weight < 1.0

    def test_recency_weight_minimum(self):
        ep = self._make_episode(hours_ago=200)  # > 7 days
        assert ep.recency_weight == 0.1

    def test_naive_timestamp_treated_as_utc(self):
        naive_ts = datetime(2020, 1, 1)
        ep = self._make_episode(timestamp=naive_ts)
        # Should not crash, age_hours should be positive
        assert ep.age_hours > 0


# ── EpisodicMemory.recall_similar tests ─────────────────────────────────────


class TestRecallSimilar:
    def _make_storage(self, actions: list[dict]) -> MagicMock:
        storage = MagicMock()
        storage.get_recent_successful_actions.return_value = actions
        return storage

    def _make_action(
        self,
        id: int = 1,
        source_probe: str = "lint_typecheck",
        category: str = "lint",
        title: str = "Type error in module X",
        result_summary: str = "Fixed type annotation",
        hours_ago: float = 2,
    ) -> dict:
        ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return {
            "id": id,
            "source_probe": source_probe,
            "category": category,
            "title": title,
            "result_summary": result_summary,
            "files_changed": ["src/module.py"],
            "details": {"source_probe": source_probe},
            "created_at": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }

    def test_same_probe_high_relevance(self):
        actions = [self._make_action(source_probe="lint_typecheck")]
        storage = self._make_storage(actions)
        mem = EpisodicMemory(storage, window_days=7, max_episodes=3)

        result = mem.recall_similar("lint_typecheck", "some issue text")
        assert len(result) == 1
        assert result[0].probe_id == "lint_typecheck"

    def test_different_probe_low_relevance_filtered(self):
        actions = [self._make_action(source_probe="health_check")]
        storage = self._make_storage(actions)
        mem = EpisodicMemory(storage, window_days=7, max_episodes=3)

        # No keyword overlap and different probe → below threshold
        result = mem.recall_similar("lint_typecheck", "unique unrelated words here")
        assert len(result) == 0

    def test_keyword_overlap_boosts_relevance(self):
        actions = [
            self._make_action(
                source_probe="other_probe",
                title="Type error in module X with bad import connection failed",
            )
        ]
        storage = self._make_storage(actions)
        mem = EpisodicMemory(storage, window_days=7, max_episodes=3)

        # Keywords overlap: type, error, in, module, x, with, bad = 7 tokens × 0.05 = 0.35 > 0.3
        result = mem.recall_similar(
            "different_probe", "Type error in module X with bad configuration"
        )
        assert len(result) == 1

    def test_max_episodes_limit(self):
        actions = [
            self._make_action(id=i, source_probe="lint_typecheck", hours_ago=i)
            for i in range(1, 10)
        ]
        storage = self._make_storage(actions)
        mem = EpisodicMemory(storage, window_days=7, max_episodes=3)

        result = mem.recall_similar("lint_typecheck", "some issue")
        assert len(result) <= 3

    def test_empty_storage_returns_empty(self):
        storage = self._make_storage([])
        mem = EpisodicMemory(storage, window_days=7, max_episodes=3)

        result = mem.recall_similar("any_probe", "any text")
        assert result == []

    def test_storage_exception_returns_empty(self):
        storage = MagicMock()
        storage.get_recent_successful_actions.side_effect = RuntimeError("DB error")
        mem = EpisodicMemory(storage, window_days=7, max_episodes=3)

        result = mem.recall_similar("probe", "text")
        assert result == []

    def test_malformed_action_skipped(self):
        actions = [
            {"id": 1},  # Missing most fields
            self._make_action(id=2, source_probe="lint_typecheck"),
        ]
        storage = self._make_storage(actions)
        mem = EpisodicMemory(storage, window_days=7, max_episodes=3)

        result = mem.recall_similar("lint_typecheck", "some issue")
        # Should still find the valid action
        assert len(result) >= 1


# ── EpisodicMemory.format_for_prompt tests ──────────────────────────────────


class TestFormatForPrompt:
    def _make_episode(self, **kwargs) -> Episode:
        defaults = {
            "action_id": "1",
            "probe_id": "lint_typecheck",
            "issue_category": "lint",
            "issue_summary": "Type error in module X",
            "fix_summary": "Fixed type annotation",
            "files_changed": ["src/module.py"],
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=2),
            "success": True,
        }
        defaults.update(kwargs)
        return Episode(**defaults)

    def test_empty_episodes_returns_empty_string(self):
        mem = EpisodicMemory(MagicMock())
        assert mem.format_for_prompt([]) == ""

    def test_format_single_episode(self):
        ep = self._make_episode()
        mem = EpisodicMemory(MagicMock())
        result = mem.format_for_prompt([ep])

        assert "## Recent Similar Fixes (Episodic Memory)" in result
        assert "Episode 1" in result
        assert "lint_typecheck" in result
        assert "Type error in module X" in result
        assert "Fixed type annotation" in result
        assert "src/module.py" in result

    def test_format_multiple_episodes(self):
        episodes = [
            self._make_episode(action_id="1", probe_id="probe_a"),
            self._make_episode(action_id="2", probe_id="probe_b"),
        ]
        mem = EpisodicMemory(MagicMock())
        result = mem.format_for_prompt(episodes)

        assert "Episode 1" in result
        assert "Episode 2" in result
        assert "probe_a" in result
        assert "probe_b" in result

    def test_format_truncates_long_text(self):
        long_summary = "x" * 500
        ep = self._make_episode(issue_summary=long_summary)
        mem = EpisodicMemory(MagicMock())
        result = mem.format_for_prompt([ep])

        # issue_summary truncated to 200
        assert len(long_summary) > 200
        assert "x" * 200 in result
        assert "x" * 201 not in result

    def test_format_no_files(self):
        ep = self._make_episode(files_changed=[])
        mem = EpisodicMemory(MagicMock())
        result = mem.format_for_prompt([ep])

        assert "**Files**" not in result

    def test_format_age_just_now(self):
        ep = self._make_episode(
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=30)
        )
        mem = EpisodicMemory(MagicMock())
        result = mem.format_for_prompt([ep])
        assert "just now" in result

    def test_format_age_hours(self):
        ep = self._make_episode(
            timestamp=datetime.now(timezone.utc) - timedelta(hours=5)
        )
        mem = EpisodicMemory(MagicMock())
        result = mem.format_for_prompt([ep])
        assert "5h ago" in result

    def test_format_age_days(self):
        ep = self._make_episode(
            timestamp=datetime.now(timezone.utc) - timedelta(days=3)
        )
        mem = EpisodicMemory(MagicMock())
        result = mem.format_for_prompt([ep])
        assert "3d ago" in result


# ── Relevance calculation tests ─────────────────────────────────────────────


class TestRelevanceCalculation:
    def test_same_probe_gives_0_6(self):
        mem = EpisodicMemory(MagicMock())
        ep = Episode(
            action_id="1",
            probe_id="lint_typecheck",
            issue_category="lint",
            issue_summary="no overlap words here",
            fix_summary="fix",
            files_changed=[],
            timestamp=datetime.now(timezone.utc),
            success=True,
        )
        score = mem._calculate_relevance(ep, "lint_typecheck", "completely different")
        assert score == pytest.approx(0.6, abs=0.01)

    def test_keyword_overlap_adds_score(self):
        mem = EpisodicMemory(MagicMock())
        ep = Episode(
            action_id="1",
            probe_id="other",
            issue_category="lint",
            issue_summary="error in module database connection",
            fix_summary="fix",
            files_changed=[],
            timestamp=datetime.now(timezone.utc),
            success=True,
        )
        score = mem._calculate_relevance(
            ep, "other", "error in module database connection"
        )
        # 5 word overlap × 0.05 = 0.25, but different probe so no +0.6
        # Wait - "other" == "other" so +0.6 + overlap
        assert score > 0.6

    def test_no_overlap_different_probe_is_zero(self):
        mem = EpisodicMemory(MagicMock())
        ep = Episode(
            action_id="1",
            probe_id="probe_a",
            issue_category="lint",
            issue_summary="completely unique words xyz",
            fix_summary="fix",
            files_changed=[],
            timestamp=datetime.now(timezone.utc),
            success=True,
        )
        score = mem._calculate_relevance(ep, "probe_b", "totally different abc")
        assert score == 0.0

    def test_relevance_capped_at_1(self):
        mem = EpisodicMemory(MagicMock())
        ep = Episode(
            action_id="1",
            probe_id="same",
            issue_category="lint",
            issue_summary="a b c d e f g h i j k l m n o p",
            fix_summary="fix",
            files_changed=[],
            timestamp=datetime.now(timezone.utc),
            success=True,
        )
        score = mem._calculate_relevance(
            ep, "same", "a b c d e f g h i j k l m n o p"
        )
        assert score <= 1.0
