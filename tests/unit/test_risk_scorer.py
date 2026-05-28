"""Tests for vivify/harness/risk_scorer.py — RiskScorer."""
from __future__ import annotations

import pytest

from vivify.config.schema import HarnessConfig
from vivify.harness.models import RiskAssessment
from vivify.harness.risk_scorer import RiskScorer


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def config():
    """HarnessConfig with risk scoring enabled."""
    return HarnessConfig(risk_scoring_enabled=True)


@pytest.fixture
def scorer(config):
    return RiskScorer(config)


@pytest.fixture
def disabled_config():
    """HarnessConfig with risk scoring disabled."""
    return HarnessConfig(risk_scoring_enabled=False)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestRiskLevels:
    """Tests for risk scoring levels."""

    def test_low_risk_simple_file(self, scorer):
        """Ordinary .py file modification → low risk."""
        assessment = scorer.assess_risk(["src/utils.py"])
        assert assessment.level == "low"
        assert assessment.score <= 20

    def test_high_risk_dependency_file(self, scorer):
        """requirements.txt → +20 score."""
        assessment = scorer.assess_risk(["requirements.txt"])
        assert assessment.score >= 20
        assert any("dependency" in f for f in assessment.factors)

    def test_high_risk_migration(self, scorer):
        """Migration file → +20 score."""
        assessment = scorer.assess_risk(["alembic/versions/001_init.py"])
        assert assessment.score >= 20
        assert any("migration" in f for f in assessment.factors)

    def test_medium_risk_config(self, scorer):
        """YAML config file → +15 score."""
        assessment = scorer.assess_risk(["config/settings.yml"])
        assert assessment.score >= 15
        assert any("config" in f for f in assessment.factors)

    def test_api_route_file(self, scorer):
        """Routes/API path → +10 score."""
        assessment = scorer.assess_risk(["src/routes/users.py"])
        assert assessment.score >= 10
        assert any("api" in f for f in assessment.factors)

    def test_large_diff_score(self, scorer):
        """>100 lines changed → +10 score."""
        assessment = scorer.assess_risk(
            ["src/main.py"],
            diff_stats={"lines_added": 80, "lines_deleted": 30},
        )
        assert assessment.score >= 10
        assert any("large_diff" in f for f in assessment.factors)

    def test_deleted_files_score(self, scorer):
        """Deleted files → +10 score."""
        assessment = scorer.assess_risk(
            ["src/main.py"],
            diff_stats={"files_deleted": ["old_module.py"]},
        )
        assert assessment.score >= 10
        assert any("deleted" in f for f in assessment.factors)

    def test_combined_high_risk(self, scorer):
        """Multiple factors stacked → score>40 = high."""
        assessment = scorer.assess_risk(
            ["requirements.txt", "alembic/versions/002_add.py", "src/routes/api.py"],
            diff_stats={"lines_added": 200, "lines_deleted": 0},
        )
        assert assessment.score > 40
        assert assessment.level == "high"


class TestRiskDisabled:
    """Tests for disabled risk scoring."""

    def test_risk_disabled_returns_low(self, disabled_config):
        """When risk_scoring_enabled=False, always returns low."""
        scorer = RiskScorer(disabled_config)
        assessment = scorer.assess_risk(
            ["requirements.txt", "alembic/versions/001.py"],
            diff_stats={"lines_added": 500, "lines_deleted": 200},
        )
        assert assessment.level == "low"
        assert assessment.score == 0


class TestVerificationRequirements:
    """Tests for get_verification_requirements."""

    def test_verification_requirements_low(self, scorer):
        """Low risk requires only lint."""
        assessment = RiskAssessment(score=10, level="low", factors=[])
        reqs = scorer.get_verification_requirements(assessment)
        assert reqs["lint"] is True
        assert reqs["typecheck"] is False
        assert reqs["test"] is False
        assert reqs["build"] is False

    def test_verification_requirements_medium(self, scorer):
        """Medium risk requires lint + test."""
        assessment = RiskAssessment(score=30, level="medium", factors=[])
        reqs = scorer.get_verification_requirements(assessment)
        assert reqs["lint"] is True
        assert reqs["test"] is True
        assert reqs["typecheck"] is False
        assert reqs["build"] is False

    def test_verification_requirements_high(self, scorer):
        """High risk requires all sensors."""
        assessment = RiskAssessment(score=50, level="high", factors=[])
        reqs = scorer.get_verification_requirements(assessment)
        assert reqs["lint"] is True
        assert reqs["typecheck"] is True
        assert reqs["test"] is True
        assert reqs["build"] is True
