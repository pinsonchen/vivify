"""Tests for vivify/harness/guides.py — GuidesManager and Guide."""
from __future__ import annotations

from pathlib import Path

import pytest

from vivify.harness.guides import Guide, GuidesManager


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def guides_dir(tmp_path):
    """Create a temporary guides directory with sample files."""
    d = tmp_path / "guides"
    d.mkdir()
    (d / "always_code_style.md").write_text("# Code Style\nFollow PEP8.", encoding="utf-8")
    (d / "always_testing.md").write_text("# Testing\nWrite tests.", encoding="utf-8")
    (d / "fix_scope.md").write_text("# Fix Scope\nKeep it minimal.", encoding="utf-8")
    (d / "develop_guidelines.md").write_text("# Develop\nPlan first.", encoding="utf-8")
    return d


@pytest.fixture
def manager(guides_dir):
    return GuidesManager(guides_dir)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestLoadGuides:
    """Tests for load_guides method."""

    def test_load_guides_from_directory(self, manager, guides_dir):
        """Correctly loads all .md files."""
        guides = manager.load_guides()
        assert len(guides) == 4
        names = [g.name for g in guides]
        assert "always_code_style" in names
        assert "fix_scope" in names

    def test_load_guides_empty_directory(self, tmp_path):
        """Empty directory returns empty list."""
        empty_dir = tmp_path / "empty_guides"
        empty_dir.mkdir()
        mgr = GuidesManager(empty_dir)
        guides = mgr.load_guides()
        assert guides == []

    def test_load_guides_nonexistent_directory(self, tmp_path):
        """Non-existent directory does not raise error; returns empty."""
        mgr = GuidesManager(tmp_path / "nonexistent")
        guides = mgr.load_guides()
        assert guides == []


class TestGetGuidesForCategory:
    """Tests for get_guides_for_category method."""

    def test_get_guides_for_fix_category(self, manager):
        """fix_issue matches always_* + fix_*."""
        text = manager.get_guides_for_category("fix_issue")
        assert "Code Style" in text
        assert "Testing" in text
        assert "Fix Scope" in text
        assert "Develop" not in text

    def test_get_guides_for_develop_category(self, manager):
        """develop_feature matches always_* + develop_*."""
        text = manager.get_guides_for_category("develop_feature")
        assert "Code Style" in text
        assert "Develop" in text
        assert "Fix Scope" not in text


class TestGuidesLengthTruncation:
    """Tests for guide text truncation."""

    def test_guides_length_truncation(self, tmp_path):
        """Combined guides exceeding MAX_GUIDES_LENGTH are truncated."""
        d = tmp_path / "big_guides"
        d.mkdir()
        # Create a large guide exceeding 1000 chars
        (d / "always_big.md").write_text("X" * 1200, encoding="utf-8")
        mgr = GuidesManager(d)
        text = mgr.get_guides_for_category("fix_issue")
        assert len(text) <= GuidesManager.MAX_GUIDES_LENGTH + 50  # allow some overhead
        assert "truncated" in text


class TestExtractCategory:
    """Tests for _extract_category private method."""

    def test_extract_category_from_filename(self, manager):
        """Filename prefix is correctly extracted."""
        assert manager._extract_category("always_code_style") == "always"
        assert manager._extract_category("fix_scope") == "fix"
        assert manager._extract_category("develop_guidelines") == "develop"
        assert manager._extract_category("verify_check") == "verify"
        assert manager._extract_category("evaluate_perf") == "evaluate"
        # Unknown prefix falls back to "always"
        assert manager._extract_category("unknown_name") == "always"


class TestNormalizeCategory:
    """Tests for _normalize_category private method."""

    def test_normalize_category(self, manager):
        """Normalize fix_issue→fix, develop_feature→develop."""
        assert manager._normalize_category("fix_issue") == "fix"
        assert manager._normalize_category("develop_feature") == "develop"
        assert manager._normalize_category("verify_feature") == "verify"
        assert manager._normalize_category("evaluate_feature") == "evaluate"


class TestGenerateDefaultGuides:
    """Tests for generate_default_guides method."""

    def test_generate_default_guides(self, tmp_path):
        """Generates 3 default guide files."""
        d = tmp_path / "gen_guides"
        mgr = GuidesManager(d)
        mgr.generate_default_guides({"language": "python"})

        assert (d / "always_code_style.md").exists()
        assert (d / "always_testing.md").exists()
        assert (d / "fix_scope.md").exists()

    def test_generate_default_guides_with_conventions(self, tmp_path):
        """Conventions content is written into code style guide."""
        d = tmp_path / "conv_guides"
        mgr = GuidesManager(d)
        mgr.generate_default_guides({"conventions": "Use 4-space indentation"})

        content = (d / "always_code_style.md").read_text(encoding="utf-8")
        assert "Use 4-space indentation" in content
