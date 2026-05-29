"""Tests for vivify.intelligence.epigenetics — epigenetic regulation layer."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from vivify.intelligence.epigenetics import (
    PROTECTED_PROBES,
    EpigeneticsEngine,
    Epigenome,
    EnvironmentImprint,
    ProbeExpression,
)


# ─── ProbeExpression tests ─────────────────────────────────────────────────────


class TestProbeExpression:
    def test_upregulate_increases_multipliers(self):
        pe = ProbeExpression(probe_id="test_probe")
        pe.upregulate(learning_rate=1.0)
        assert pe.frequency_multiplier == pytest.approx(1.1)
        assert pe.weight_multiplier == pytest.approx(1.05)
        assert pe.hit_count == 1
        assert pe.miss_streak == 0

    def test_upregulate_caps_at_3(self):
        pe = ProbeExpression(probe_id="test_probe", frequency_multiplier=2.95, weight_multiplier=2.98)
        pe.upregulate(learning_rate=1.0)
        assert pe.frequency_multiplier == 3.0
        assert pe.weight_multiplier == 3.0

    def test_upregulate_resets_miss_streak(self):
        pe = ProbeExpression(probe_id="test_probe", miss_streak=15)
        pe.upregulate(learning_rate=1.0)
        assert pe.miss_streak == 0

    def test_downregulate_no_decay_before_threshold(self):
        pe = ProbeExpression(probe_id="test_probe", frequency_multiplier=1.0)
        for _ in range(9):
            pe.downregulate(learning_rate=1.0, min_miss_streak=10)
        assert pe.frequency_multiplier == 1.0  # No decay yet
        assert pe.miss_streak == 9

    def test_downregulate_decays_after_threshold(self):
        pe = ProbeExpression(probe_id="test_probe", frequency_multiplier=1.0, miss_streak=9)
        pe.downregulate(learning_rate=1.0, min_miss_streak=10)
        assert pe.miss_streak == 10
        assert pe.frequency_multiplier < 1.0

    def test_downregulate_floors_at_minimum(self):
        pe = ProbeExpression(probe_id="test_probe", frequency_multiplier=0.12, miss_streak=100)
        pe.downregulate(learning_rate=1.0, min_miss_streak=10)
        assert pe.frequency_multiplier >= 0.1
        assert pe.weight_multiplier >= 0.2

    def test_downregulate_protected_probe_floors_at_1(self):
        for probe_id in PROTECTED_PROBES:
            pe = ProbeExpression(probe_id=probe_id, frequency_multiplier=1.0, miss_streak=50)
            pe.downregulate(learning_rate=1.0, min_miss_streak=10)
            assert pe.frequency_multiplier >= 1.0
            assert pe.weight_multiplier >= 1.0

    def test_learning_rate_affects_upregulate_speed(self):
        pe_fast = ProbeExpression(probe_id="a")
        pe_slow = ProbeExpression(probe_id="b")
        pe_fast.upregulate(learning_rate=1.0)
        pe_slow.upregulate(learning_rate=0.2)
        assert pe_fast.frequency_multiplier > pe_slow.frequency_multiplier


# ─── Epigenome tests ───────────────────────────────────────────────────────────


class TestEpigenome:
    def test_learning_rate_during_plasticity(self):
        eg = Epigenome(plasticity_window=50, total_rounds=25)
        assert eg.learning_rate == 1.0

    def test_learning_rate_at_boundary(self):
        eg = Epigenome(plasticity_window=50, total_rounds=50)
        assert eg.learning_rate == 1.0

    def test_learning_rate_decays_after_plasticity(self):
        eg = Epigenome(plasticity_window=50, total_rounds=100)
        assert eg.learning_rate < 1.0
        assert eg.learning_rate > 0.2

    def test_learning_rate_floors_at_0_2(self):
        eg = Epigenome(plasticity_window=50, total_rounds=500)
        assert eg.learning_rate == 0.2

    def test_is_plastic_true_within_window(self):
        eg = Epigenome(plasticity_window=50, total_rounds=30)
        assert eg.is_plastic is True

    def test_is_plastic_false_after_window(self):
        eg = Epigenome(plasticity_window=50, total_rounds=51)
        assert eg.is_plastic is False


# ─── EnvironmentImprint tests ──────────────────────────────────────────────────


class TestEnvironmentImprint:
    def test_sensitivity_boost(self):
        imprint = EnvironmentImprint(
            category="ci_failure", intensity=0.8, imprinted_at_round=5,
        )
        assert imprint.sensitivity_boost == pytest.approx(0.4)

    def test_max_sensitivity_boost(self):
        imprint = EnvironmentImprint(
            category="security", intensity=1.0, imprinted_at_round=1,
        )
        assert imprint.sensitivity_boost == pytest.approx(0.5)


# ─── EpigeneticsEngine tests ──────────────────────────────────────────────────


class TestEpigeneticsEngine:
    @pytest.fixture
    def engine(self, tmp_path: Path) -> EpigeneticsEngine:
        return EpigeneticsEngine(
            vivify_dir=tmp_path,
            plasticity_window=50,
            imprint_threshold=3,
            min_miss_streak=10,
        )

    def test_get_probe_multiplier_unknown_probe(self, engine: EpigeneticsEngine):
        freq, weight = engine.get_probe_multiplier("unknown_probe")
        assert freq == 1.0
        assert weight == 1.0

    def test_record_probe_result_creates_expression(self, engine: EpigeneticsEngine):
        engine.record_probe_result("ci_status", found_issues=True)
        freq, weight = engine.get_probe_multiplier("ci_status")
        assert freq > 1.0
        assert weight > 1.0

    def test_record_probe_result_downregulates_on_miss(self, engine: EpigeneticsEngine):
        # Record enough misses to trigger downregulation
        for _ in range(15):
            engine.record_probe_result("doc_staleness", found_issues=False)
        freq, weight = engine.get_probe_multiplier("doc_staleness")
        assert freq < 1.0

    def test_advance_round_increments_counter(self, engine: EpigeneticsEngine):
        assert engine.epigenome.total_rounds == 0
        engine.advance_round()
        assert engine.epigenome.total_rounds == 1
        engine.advance_round()
        assert engine.epigenome.total_rounds == 2

    def test_imprint_forms_after_threshold_hits_in_plasticity(self, engine: EpigeneticsEngine):
        # Ensure we're in plasticity window
        assert engine.epigenome.is_plastic
        # Hit ci_status 3 times (threshold)
        for _ in range(3):
            engine.record_probe_result("ci_status", found_issues=True)
        # Should have formed an imprint
        assert len(engine.epigenome.imprints) == 1
        imprint = engine.epigenome.imprints[0]
        assert imprint["category"] == "ci_failure"
        assert imprint["intensity"] > 0

    def test_no_imprint_after_plasticity_window(self, engine: EpigeneticsEngine):
        # Move past plasticity window
        engine.epigenome.total_rounds = 100
        # Hit many times
        for _ in range(10):
            engine.record_probe_result("ci_status", found_issues=True)
        # Should NOT form imprint (past plasticity)
        assert len(engine.epigenome.imprints) == 0

    def test_imprint_boost_applied_to_multiplier(self, engine: EpigeneticsEngine):
        # Form an imprint
        for _ in range(5):
            engine.record_probe_result("ci_status", found_issues=True)
        # Get multiplier - should include imprint boost
        freq, weight = engine.get_probe_multiplier("ci_status")
        # Without imprint: freq ~ 1.5, with boost it should be higher
        base_expr = engine.epigenome.probe_expressions["ci_status"]
        assert freq > base_expr.frequency_multiplier

    def test_imprint_boost_capped_at_50_percent(self, engine: EpigeneticsEngine):
        # Manually inject a high-intensity imprint
        engine.epigenome.imprints.append({
            "category": "ci_failure",
            "intensity": 1.0,
            "imprinted_at_round": 1,
            "description": "test",
        })
        engine.record_probe_result("ci_status", found_issues=True)
        freq, weight = engine.get_probe_multiplier("ci_status")
        base = engine.epigenome.probe_expressions["ci_status"]
        # Boost should be max 50%
        assert freq <= base.frequency_multiplier * 1.5 + 0.01

    def test_serialization_roundtrip(self, tmp_path: Path):
        engine = EpigeneticsEngine(
            vivify_dir=tmp_path, plasticity_window=30,
            imprint_threshold=2, min_miss_streak=5,
        )
        # Add some state
        engine.record_probe_result("ci_status", found_issues=True)
        engine.record_probe_result("ci_status", found_issues=True)
        engine.record_probe_result("lint_typecheck", found_issues=False)
        engine.advance_round()

        # Create new engine from same path
        engine2 = EpigeneticsEngine(
            vivify_dir=tmp_path, plasticity_window=30,
            imprint_threshold=2, min_miss_streak=5,
        )
        assert engine2.epigenome.total_rounds == 1
        assert "ci_status" in engine2.epigenome.probe_expressions
        ci_expr = engine2.epigenome.probe_expressions["ci_status"]
        assert ci_expr.hit_count == 2
        assert ci_expr.frequency_multiplier > 1.0

    def test_epigenome_json_file_created(self, tmp_path: Path):
        engine = EpigeneticsEngine(vivify_dir=tmp_path)
        engine.advance_round()
        json_path = tmp_path / "epigenome.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["total_rounds"] == 1
        assert "probe_expressions" in data
        assert "imprints" in data

    def test_get_status(self, engine: EpigeneticsEngine):
        engine.record_probe_result("ci_status", found_issues=True)
        status = engine.get_status()
        assert "total_rounds" in status
        assert "learning_rate" in status
        assert "is_plastic" in status
        assert "active_expressions" in status
        assert "imprints" in status
        assert status["active_expressions"] == 1

    def test_protected_probe_never_below_1(self, engine: EpigeneticsEngine):
        """Protected probes (secrets_scan, dependency_vulnerabilities) never go below 1.0."""
        for probe_id in PROTECTED_PROBES:
            for _ in range(100):
                engine.record_probe_result(probe_id, found_issues=False)
            freq, weight = engine.get_probe_multiplier(probe_id)
            assert freq >= 1.0
            assert weight >= 1.0

    def test_no_duplicate_imprints(self, engine: EpigeneticsEngine):
        """Same category should not create duplicate imprints."""
        for _ in range(10):
            engine.record_probe_result("ci_status", found_issues=True)
        ci_imprints = [
            i for i in engine.epigenome.imprints
            if i.get("category") == "ci_failure"
        ]
        assert len(ci_imprints) == 1

    def test_corrupted_json_creates_fresh_epigenome(self, tmp_path: Path):
        """When epigenome.json is corrupted, a fresh epigenome is created."""
        json_path = tmp_path / "epigenome.json"
        json_path.write_text("not valid json{{{")
        engine = EpigeneticsEngine(vivify_dir=tmp_path)
        assert engine.epigenome.total_rounds == 0
        assert len(engine.epigenome.probe_expressions) == 0

    def test_lineage_preserved(self, tmp_path: Path):
        """Lineage field is preserved across serialization."""
        engine = EpigeneticsEngine(vivify_dir=tmp_path)
        engine.epigenome.lineage = "parent-project-v1"
        engine.advance_round()

        engine2 = EpigeneticsEngine(vivify_dir=tmp_path)
        assert engine2.epigenome.lineage == "parent-project-v1"
