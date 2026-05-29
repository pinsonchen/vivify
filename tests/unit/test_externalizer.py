"""Tests for the capability externalization mechanism."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from vivify.capsules.externalizer import CapabilityExternalizer, ExternalizationPlan
from vivify.capsules.models import SkillCapsule
from vivify.capsules.store import CapsuleStore


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_capsule(
    *,
    capsule_id: str = "cap-001",
    probe_id: str = "lint_typecheck",
    issue_category: str = "lint",
    fix_strategy: str = "run ruff --fix .",
    success_count: int = 4,
    failure_count: int = 0,
    status: str = "active",
) -> SkillCapsule:
    return SkillCapsule(
        capsule_id=capsule_id,
        trigger_pattern=f"{probe_id} error pattern",
        fix_strategy=fix_strategy,
        prompt_template="Fix the lint error by running ruff",
        source_action_id="action-001",
        probe_id=probe_id,
        issue_category=issue_category,
        success_count=success_count,
        failure_count=failure_count,
        status=status,
    )


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def externalizer(tmp_project: Path) -> CapabilityExternalizer:
    return CapabilityExternalizer(
        project_root=tmp_project,
        output_dir=str(tmp_project / ".vivify" / "externalized"),
    )


@pytest.fixture
def capsule_store(tmp_path: Path) -> CapsuleStore:
    return CapsuleStore(tmp_path / "capsules")


# ── _select_target_type tests ─────────────────────────────────────────────────


class TestSelectTargetType:
    """Test target type classification logic."""

    def test_lint_category_returns_pre_commit_hook(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="lint", fix_strategy="autofix linting")
        assert externalizer._select_target_type(cap) == "pre_commit_hook"

    def test_format_strategy_returns_pre_commit_hook(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="code_quality", fix_strategy="format with black")
        assert externalizer._select_target_type(cap) == "pre_commit_hook"

    def test_style_returns_pre_commit_hook(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="style", fix_strategy="fix indentation")
        assert externalizer._select_target_type(cap) == "pre_commit_hook"

    def test_test_category_returns_github_action(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="test", fix_strategy="retry flaky tests")
        assert externalizer._select_target_type(cap) == "github_action"

    def test_ci_strategy_returns_github_action(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="pipeline", fix_strategy="fix CI timeout")
        assert externalizer._select_target_type(cap) == "github_action"

    def test_coverage_returns_github_action(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="quality", fix_strategy="increase coverage")
        assert externalizer._select_target_type(cap) == "github_action"

    def test_build_category_returns_makefile(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="build", fix_strategy="fix compilation")
        assert externalizer._select_target_type(cap) == "makefile"

    def test_compile_strategy_returns_makefile(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="generic", fix_strategy="compile assets")
        assert externalizer._select_target_type(cap) == "makefile"

    def test_bundle_returns_makefile(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="generic", fix_strategy="bundle webpack output")
        assert externalizer._select_target_type(cap) == "makefile"

    def test_generic_returns_script(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="security", fix_strategy="rotate secrets")
        assert externalizer._select_target_type(cap) == "script"

    def test_empty_category_returns_script(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="", fix_strategy="do something")
        assert externalizer._select_target_type(cap) == "script"


# ── Generator output tests ────────────────────────────────────────────────────


class TestGenerators:
    """Test that each generator produces valid output."""

    def test_github_action_format(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(probe_id="ci_status", issue_category="test", fix_strategy="run pytest")
        plan = externalizer._generate_github_action(cap)

        assert plan.target_type == "github_action"
        assert plan.capsule_id == "cap-001"
        assert "ci_status" in plan.file_path
        assert ".github/workflows/" in plan.file_path
        assert "actions/checkout@v4" in plan.content
        assert "on: [push, pull_request]" in plan.content
        assert "run pytest" in plan.content

    def test_pre_commit_hook_format(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(probe_id="lint_check", issue_category="lint", fix_strategy="ruff check .")
        plan = externalizer._generate_pre_commit_hook(cap)

        assert plan.target_type == "pre_commit_hook"
        assert "vivify-lint_check" in plan.content
        assert "repos:" in plan.content
        assert "language: system" in plan.content
        assert "ruff check ." in plan.content

    def test_makefile_target_format(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(probe_id="build_check", issue_category="build", fix_strategy="npm run build")
        plan = externalizer._generate_makefile_target(cap)

        assert plan.target_type == "makefile"
        assert ".PHONY: vivify-build_check" in plan.content
        assert "npm run build" in plan.content
        assert "make.mk" in plan.file_path

    def test_script_format(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(probe_id="sec_fix", issue_category="security", fix_strategy="scan secrets")
        plan = externalizer._generate_script(cap)

        assert plan.target_type == "script"
        assert "#!/usr/bin/env bash" in plan.content
        assert "set -euo pipefail" in plan.content
        assert "scan secrets" in plan.content
        assert "fix.sh" in plan.file_path


# ── ExternalizationPlan properties ────────────────────────────────────────────


class TestExternalizationPlan:
    """Test ExternalizationPlan dataclass properties."""

    def test_commit_message(self):
        plan = ExternalizationPlan(
            capsule_id="cap-001",
            capsule_title="lint error fix",
            target_type="pre_commit_hook",
            file_path=".vivify/externalized/lint-hook.yml",
            content="hook config",
            description="Pre-commit hook for lint fix",
        )
        msg = plan.commit_message
        assert "chore:" in msg
        assert "lint error fix" in msg
        assert "pre_commit_hook" in msg


# ── generate_plan routing ─────────────────────────────────────────────────────


class TestGeneratePlan:
    """Test that generate_plan correctly routes to the right generator."""

    def test_lint_capsule_gets_pre_commit(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="lint")
        plan = externalizer.generate_plan(cap)
        assert plan is not None
        assert plan.target_type == "pre_commit_hook"

    def test_test_capsule_gets_github_action(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="test", fix_strategy="run test suite")
        plan = externalizer.generate_plan(cap)
        assert plan is not None
        assert plan.target_type == "github_action"

    def test_build_capsule_gets_makefile(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="build")
        plan = externalizer.generate_plan(cap)
        assert plan is not None
        assert plan.target_type == "makefile"

    def test_generic_capsule_gets_script(self, externalizer: CapabilityExternalizer):
        cap = _make_capsule(issue_category="security", fix_strategy="rotate creds")
        plan = externalizer.generate_plan(cap)
        assert plan is not None
        assert plan.target_type == "script"


# ── get_promotion_candidates integration ──────────────────────────────────────


class TestGetPromotionCandidates:
    """Test integration with CapsuleStore.get_promotion_candidates."""

    def test_returns_promotable_capsules(
        self, externalizer: CapabilityExternalizer, capsule_store: CapsuleStore
    ):
        # Promotable: success_count=4 >= 3, effectiveness=1.0 >= 0.7
        cap = _make_capsule(success_count=4, failure_count=0)
        capsule_store.save(cap)

        candidates = externalizer.get_promotion_candidates(capsule_store)
        assert len(candidates) == 1
        assert candidates[0].capsule_id == "cap-001"

    def test_excludes_non_promotable(
        self, externalizer: CapabilityExternalizer, capsule_store: CapsuleStore
    ):
        # Not promotable: success_count=1 < 3
        cap = _make_capsule(success_count=1, failure_count=0)
        capsule_store.save(cap)

        candidates = externalizer.get_promotion_candidates(capsule_store)
        assert len(candidates) == 0

    def test_excludes_already_promoted(
        self, externalizer: CapabilityExternalizer, capsule_store: CapsuleStore
    ):
        cap = _make_capsule(success_count=5, failure_count=0, status="promoted")
        capsule_store.save(cap)

        candidates = externalizer.get_promotion_candidates(capsule_store)
        assert len(candidates) == 0


# ── write_plan ────────────────────────────────────────────────────────────────


class TestWritePlan:
    """Test that write_plan creates files on disk."""

    def test_writes_file(self, externalizer: CapabilityExternalizer, tmp_project: Path):
        plan = ExternalizationPlan(
            capsule_id="cap-001",
            capsule_title="test fix",
            target_type="script",
            file_path="test-fix.sh",
            content="#!/bin/bash\necho hello\n",
            description="Test script",
        )

        written = externalizer.write_plan(plan)
        assert written.exists()
        assert written.read_text() == "#!/bin/bash\necho hello\n"


# ── full run integration ──────────────────────────────────────────────────────


class TestRun:
    """Test the full run() method."""

    def test_run_with_candidates(self, tmp_project: Path):
        capsule_dir = tmp_project / "capsules"
        store = CapsuleStore(capsule_dir)
        ext = CapabilityExternalizer(
            project_root=tmp_project,
            output_dir=str(tmp_project / ".vivify" / "externalized"),
        )

        # Create promotable capsule
        cap = _make_capsule(success_count=4, failure_count=0)
        store.save(cap)

        plans = ext.run(store)
        assert len(plans) == 1
        assert plans[0].target_type == "pre_commit_hook"  # lint category

        # Capsule should be marked as promoted
        reloaded = store.load("cap-001")
        assert reloaded is not None
        assert reloaded.status == "promoted"
        assert reloaded.promoted_to == "pre_commit_hook"

        # File should exist on disk
        output_dir = tmp_project / ".vivify" / "externalized"
        files = list(output_dir.iterdir())
        assert len(files) == 1

    def test_run_no_candidates(self, tmp_project: Path):
        capsule_dir = tmp_project / "capsules"
        store = CapsuleStore(capsule_dir)
        ext = CapabilityExternalizer(
            project_root=tmp_project,
            output_dir=str(tmp_project / ".vivify" / "externalized"),
        )

        # Non-promotable capsule
        cap = _make_capsule(success_count=1, failure_count=0)
        store.save(cap)

        plans = ext.run(store)
        assert plans == []

    def test_run_empty_store(self, tmp_project: Path):
        capsule_dir = tmp_project / "capsules"
        store = CapsuleStore(capsule_dir)
        ext = CapabilityExternalizer(
            project_root=tmp_project,
            output_dir=str(tmp_project / ".vivify" / "externalized"),
        )

        plans = ext.run(store)
        assert plans == []

    def test_run_multiple_candidates(self, tmp_project: Path):
        capsule_dir = tmp_project / "capsules"
        store = CapsuleStore(capsule_dir)
        ext = CapabilityExternalizer(
            project_root=tmp_project,
            output_dir=str(tmp_project / ".vivify" / "externalized"),
        )

        # Create multiple promotable capsules
        cap1 = _make_capsule(capsule_id="cap-lint", probe_id="lint", issue_category="lint", success_count=5)
        cap2 = _make_capsule(capsule_id="cap-test", probe_id="test", issue_category="test", fix_strategy="run ci tests", success_count=4)
        cap3 = _make_capsule(capsule_id="cap-build", probe_id="build", issue_category="build", fix_strategy="compile project", success_count=3)
        store.save(cap1)
        store.save(cap2)
        store.save(cap3)

        plans = ext.run(store)
        assert len(plans) == 3

        types = {p.target_type for p in plans}
        assert "pre_commit_hook" in types
        assert "github_action" in types
        assert "makefile" in types
