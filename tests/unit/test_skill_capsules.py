"""Unit tests for the Skill Capsule subsystem (models / store / extractor)."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest

from vivify.capsules import CapsuleExtractor, CapsuleStore, SkillCapsule


# ── helpers ────────────────────────────────────────────────────────────


def _make_capsule(
    *,
    probe_id: str = "lint_typecheck",
    category: str = "lint",
    trigger: str = "lint_typecheck:lint flake8 missing import",
    success: int = 0,
    failure: int = 0,
    status: str = "active",
) -> SkillCapsule:
    return SkillCapsule(
        capsule_id=uuid.uuid4().hex,
        trigger_pattern=trigger,
        fix_strategy="Add the missing import statement at the top of the module.",
        prompt_template="Previously this type of issue was fixed by: add import.",
        source_action_id="run-1",
        probe_id=probe_id,
        issue_category=category,
        success_count=success,
        failure_count=failure,
        status=status,
    )


# ── model tests ────────────────────────────────────────────────────────


class TestSkillCapsuleModel:
    def test_effectiveness_zero_when_no_usage(self):
        cap = _make_capsule()
        assert cap.effectiveness == 0.0

    def test_effectiveness_ratio(self):
        cap = _make_capsule(success=3, failure=1)
        assert cap.effectiveness == pytest.approx(0.75)

    def test_should_promote_threshold(self):
        # 3 successes + effectiveness 0.75 → promote
        cap = _make_capsule(success=3, failure=1)
        assert cap.should_promote is True

    def test_should_not_promote_when_low_effectiveness(self):
        cap = _make_capsule(success=3, failure=4)  # 0.43 < 0.7
        assert cap.should_promote is False

    def test_should_not_promote_below_success_count(self):
        cap = _make_capsule(success=2, failure=0)  # 1.0 but only 2 successes
        assert cap.should_promote is False

    def test_should_archive_threshold(self):
        cap = _make_capsule(success=5, failure=1)  # 5/6 ≈ 0.83
        assert cap.should_archive is True

    def test_should_not_archive_when_low_effectiveness(self):
        cap = _make_capsule(success=5, failure=5)
        assert cap.should_archive is False

    def test_to_from_dict_roundtrip(self):
        cap = _make_capsule(success=2, failure=1)
        cap.last_used = datetime.now()
        data = cap.to_dict()
        restored = SkillCapsule.from_dict(data)
        assert restored.capsule_id == cap.capsule_id
        assert restored.success_count == 2
        assert restored.failure_count == 1
        assert restored.last_used is not None


# ── store tests ────────────────────────────────────────────────────────


class TestCapsuleStore:
    def test_save_and_load(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        cap = _make_capsule()
        store.save(cap)
        loaded = store.load(cap.capsule_id)
        assert loaded is not None
        assert loaded.capsule_id == cap.capsule_id
        assert loaded.fix_strategy == cap.fix_strategy

    def test_load_all_skips_archived_by_default(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        active = _make_capsule(status="active")
        archived = _make_capsule(status="archived")
        store.save(active)
        store.save(archived)
        ids = {c.capsule_id for c in store.load_all()}
        assert active.capsule_id in ids
        assert archived.capsule_id not in ids

        all_ids = {c.capsule_id for c in store.load_all(include_archived=True)}
        assert archived.capsule_id in all_ids

    def test_find_matching_returns_none_when_empty(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        assert store.find_matching("any_probe", "anything") is None

    def test_find_matching_requires_probe_match(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        cap = _make_capsule(probe_id="lint_typecheck")
        store.save(cap)
        # Different probe → no match.
        assert store.find_matching("test_coverage", "missing import") is None
        # Same probe + shared keyword → match.
        match = store.find_matching("lint_typecheck", "missing import flake8")
        assert match is not None
        assert match.capsule_id == cap.capsule_id

    def test_find_matching_prefers_higher_effectiveness(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        low = _make_capsule(success=1, failure=2)         # 0.33
        high = _make_capsule(success=4, failure=1)        # 0.80
        store.save(low)
        store.save(high)
        match = store.find_matching("lint_typecheck", "lint missing import")
        assert match is not None
        assert match.capsule_id == high.capsule_id

    def test_record_usage_increments_counters(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        cap = _make_capsule()
        store.save(cap)

        store.record_usage(cap.capsule_id, success=True)
        store.record_usage(cap.capsule_id, success=False)
        reloaded = store.load(cap.capsule_id)
        assert reloaded is not None
        assert reloaded.success_count == 1
        assert reloaded.failure_count == 1
        assert reloaded.last_used is not None

    def test_record_usage_auto_archives_when_threshold_met(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        cap = _make_capsule(success=4, failure=0)
        store.save(cap)
        # 5th success should trip should_archive (5 successes, 100% effective).
        store.record_usage(cap.capsule_id, success=True)
        reloaded = store.load(cap.capsule_id)
        assert reloaded is not None
        assert reloaded.status == "archived"

    def test_record_usage_no_op_for_missing_id(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        # Should not raise.
        store.record_usage("does-not-exist", success=True)

    def test_get_promotion_candidates(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        promote = _make_capsule(success=3, failure=1)        # 0.75 → eligible
        too_few = _make_capsule(success=2, failure=0)        # not enough
        store.save(promote)
        store.save(too_few)
        ids = {c.capsule_id for c in store.get_promotion_candidates()}
        assert promote.capsule_id in ids
        assert too_few.capsule_id not in ids

    def test_corrupt_file_is_skipped(self, tmp_path: Path):
        capsules_dir = tmp_path / "capsules"
        store = CapsuleStore(capsules_dir)
        good = _make_capsule()
        store.save(good)
        # Drop a corrupt file in the directory.
        (capsules_dir / "broken.json").write_text("{not json")
        result = store.load_all()
        assert {c.capsule_id for c in result} == {good.capsule_id}


# ── extractor tests ────────────────────────────────────────────────────


class TestCapsuleExtractor:
    def test_extracts_basic_capsule_from_action_log(self):
        extractor = CapsuleExtractor()
        action_log = {
            "id": 42,
            "run_id": "run-x",
            "action_type": "heal",
            "status": "success",
            "category": "lint",
            "result_summary": "Added missing typing import to fix the flake8 error.",
            "details": {"source_probe": "lint_typecheck"},
        }
        issue = {
            "category": "lint",
            "title": "flake8 missing import in module foo",
            "description": "F401 unused import or missing import",
            "source_probe": "lint_typecheck",
        }
        cap = extractor.extract_from_fix(action_log, issue, fix_diff="")
        assert cap.probe_id == "lint_typecheck"
        assert cap.issue_category == "lint"
        assert "lint_typecheck:lint" in cap.trigger_pattern
        assert "Added missing typing import" in cap.fix_strategy
        assert "Previously this type of issue was fixed by:" in cap.prompt_template
        assert cap.source_action_id == "42"
        assert cap.success_count == 0

    def test_extractor_falls_back_to_diff(self):
        extractor = CapsuleExtractor()
        action_log = {"id": 1, "details": {}}
        issue = {"category": "build", "source_probe": "build_check"}
        diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n+print('hello')\n"
        cap = extractor.extract_from_fix(action_log, issue, fix_diff=diff)
        # Strategy should pick something non-empty even without summary.
        assert cap.fix_strategy
        assert cap.probe_id == "build_check"


# ── lifecycle test ─────────────────────────────────────────────────────


class TestCapsuleLifecycle:
    def test_active_to_archived_via_record_usage(self, tmp_path: Path):
        """active → archived after enough successful reuse."""
        store = CapsuleStore(tmp_path / "capsules")
        cap = _make_capsule(success=0, failure=0)
        store.save(cap)
        # Simulate 5 successful reuses.
        for _ in range(5):
            store.record_usage(cap.capsule_id, success=True)
        reloaded = store.load(cap.capsule_id)
        assert reloaded is not None
        assert reloaded.status == "archived"
        assert reloaded.success_count == 5

    def test_promoted_status_is_preserved_across_save(self, tmp_path: Path):
        store = CapsuleStore(tmp_path / "capsules")
        cap = _make_capsule(success=3, failure=1, status="promoted")
        cap.promoted_to = "ci/lint-step"
        store.save(cap)
        reloaded = store.load(cap.capsule_id)
        assert reloaded is not None
        assert reloaded.status == "promoted"
        assert reloaded.promoted_to == "ci/lint-step"


# ── builder integration ────────────────────────────────────────────────


class TestBuilderIntegration:
    def test_capsule_hint_appended_to_remediation(self):
        from vivify.agents.prompts import builders
        from vivify.models.issue import Issue, IssueLevel

        issue = Issue.factory(
            category="lint",
            level=IssueLevel.MEDIUM,
            title="missing import",
            description="flake8 says module foo missing",
            source_probe="lint_typecheck",
        )
        prompt_with = builders.build_fix_issue(
            issue,
            workspace="/tmp/x",
            recent_history="",
            remediation_hint="rca: prior failure",
            capsule_hint="CAPSULE_HINT_MARKER_42",
        )
        assert "CAPSULE_HINT_MARKER_42" in prompt_with

        prompt_without = builders.build_fix_issue(
            issue,
            workspace="/tmp/x",
            recent_history="",
            remediation_hint="rca: prior failure",
        )
        assert "CAPSULE_HINT_MARKER_42" not in prompt_without
