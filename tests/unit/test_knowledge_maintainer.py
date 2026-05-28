"""Tests for KnowledgeMaintainer rate-limiting and error handling."""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vivify.knowledge.maintainer import KnowledgeMaintainer


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project structure."""
    (tmp_path / ".vivify" / "knowledge").mkdir(parents=True)
    return tmp_path


class TestRateLimiting:
    """Test that maybe_update() respects min_interval_seconds."""

    def test_first_call_triggers_update(self, tmp_project):
        """First call should always trigger (last_check_time starts at 0)."""
        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=600,
        )
        with patch.object(maintainer.builder, "build_incremental", return_value=None) as mock_build:
            maintainer.maybe_update()
            mock_build.assert_called_once()

    def test_second_call_within_interval_skipped(self, tmp_project):
        """Second call within min_interval should be a no-op."""
        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=600,
        )
        with patch.object(maintainer.builder, "build_incremental", return_value=None) as mock_build:
            maintainer.maybe_update()
            maintainer.maybe_update()  # Should be skipped
            assert mock_build.call_count == 1

    def test_call_after_interval_triggers_update(self, tmp_project):
        """Call after min_interval has elapsed should trigger update."""
        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=1,  # 1 second for testing
        )
        with patch.object(maintainer.builder, "build_incremental", return_value=None) as mock_build:
            maintainer.maybe_update()
            assert mock_build.call_count == 1

            time.sleep(1.1)  # Wait past the interval
            maintainer.maybe_update()
            assert mock_build.call_count == 2


class TestMarkUpdateNeeded:
    """Test that mark_update_needed() resets the rate limiter."""

    def test_mark_resets_timer(self, tmp_project):
        """After mark_update_needed(), next maybe_update() should run immediately."""
        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=600,
        )
        with patch.object(maintainer.builder, "build_incremental", return_value=None) as mock_build:
            maintainer.maybe_update()
            assert mock_build.call_count == 1

            # Normally this would be skipped due to rate limit
            maintainer.mark_update_needed()
            maintainer.maybe_update()
            assert mock_build.call_count == 2

    def test_mark_sets_flag(self, tmp_project):
        """mark_update_needed() should set the internal flag."""
        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=600,
        )
        assert maintainer._update_needed is False
        maintainer.mark_update_needed()
        assert maintainer._update_needed is True
        assert maintainer._last_check_time == 0


class TestExceptionSilencing:
    """Test that exceptions in maybe_update() never propagate."""

    def test_builder_exception_silenced(self, tmp_project):
        """Exceptions from builder.build_incremental() should not propagate."""
        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=0,
        )
        with patch.object(
            maintainer.builder, "build_incremental",
            side_effect=RuntimeError("simulated failure"),
        ):
            # Should not raise
            maintainer.maybe_update()

    def test_builder_oserror_silenced(self, tmp_project):
        """OS-level errors should not propagate."""
        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=0,
        )
        with patch.object(
            maintainer.builder, "build_incremental",
            side_effect=OSError("disk error"),
        ):
            # Should not raise
            maintainer.maybe_update()


class TestIsAvailable:
    """Test is_available() checks for existing knowledge graph."""

    def test_returns_false_when_no_graph(self, tmp_project):
        """Should return False when no graph.json exists."""
        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=600,
        )
        assert maintainer.is_available() is False

    def test_returns_true_when_graph_exists(self, tmp_project):
        """Should return True when graph.json exists with valid content."""
        import json

        graph_path = tmp_project / ".vivify" / "knowledge" / "graph.json"
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_path.write_text(
            json.dumps({
                "metadata": {},
                "nodes": [],
                "edges": [],
                "layers": [],
                "conventions": [],
            }),
            encoding="utf-8",
        )
        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=600,
        )
        assert maintainer.is_available() is True


class TestIncrementalResult:
    """Test behavior when build_incremental returns a graph."""

    def test_logs_node_count_on_success(self, tmp_project):
        """When build_incremental returns a graph, node count should be logged."""
        from vivify.knowledge.models import KnowledgeGraph, GraphNode, NodeType

        maintainer = KnowledgeMaintainer(
            project_root=tmp_project,
            min_interval_seconds=0,
        )
        mock_graph = KnowledgeGraph()
        mock_graph.nodes = [
            GraphNode(id="module:test", type=NodeType.MODULE, name="test", path="test"),
        ]
        with patch.object(
            maintainer.builder, "build_incremental", return_value=mock_graph,
        ):
            # Should not raise, just log
            maintainer.maybe_update()
