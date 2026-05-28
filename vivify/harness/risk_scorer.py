"""Risk scoring engine for AI code modifications.

This module implements heuristic-based risk assessment for AI-generated code
changes. The :class:`RiskScorer` inspects the list of modified files plus
optional diff statistics, accumulates a numeric score from a fixed set of
risk factors, and converts it to a discrete risk level (``low`` / ``medium``
/ ``high``). The harness sub-module uses the resulting
:class:`~vivify.harness.models.RiskAssessment` to decide which sensors must
run during the PEV verification loop.
"""
from __future__ import annotations

import logging
from pathlib import Path

from vivify.config.schema import HarnessConfig
from vivify.harness.models import RiskAssessment

logger = logging.getLogger(__name__)


# File patterns indicating high-risk modifications
DEPENDENCY_FILES = {
    "requirements.txt", "requirements-dev.txt", "setup.py", "setup.cfg",
    "pyproject.toml", "Pipfile", "Pipfile.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
    "Gemfile", "Gemfile.lock",
}

CONFIG_FILES = {
    ".env", ".env.local", ".env.production",
    "docker-compose.yml", "docker-compose.yaml", "Dockerfile",
    ".github/workflows", "Makefile", "Procfile",
}

CONFIG_EXTENSIONS = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf"}

API_INDICATORS = {
    "routes", "router", "endpoint", "api", "views", "controllers",
    "handler", "middleware", "schema", "serializer",
}

MIGRATION_INDICATORS = {"migration", "migrations", "alembic", "migrate"}


class RiskScorer:
    """Assess risk level of AI code modifications.

    Risk factors and weights:

    - Dependency file modified: +20
    - Config file modified: +15
    - Core module modified (high edge count in knowledge graph): +15
    - Large diff (> 100 lines): +10
    - File deleted: +10
    - API/route definition modified: +10
    - Database migration modified: +20

    Risk levels:

    - 0-20: ``low`` (auto-pass, lint only)
    - 21-40: ``medium`` (run lint + test)
    - 41+: ``high`` (run all sensors, must pass for PR)
    """

    def __init__(self, config: HarnessConfig):
        self._config = config

    def assess_risk(
        self,
        changed_files: list[str],
        diff_stats: dict | None = None,
    ) -> RiskAssessment:
        """Assess the risk of a code change.

        Args:
            changed_files: List of file paths that were modified.
            diff_stats: Optional dict with keys like:

                - ``lines_added``: int
                - ``lines_deleted``: int
                - ``files_deleted``: list[str]

        Returns:
            :class:`RiskAssessment` with score, level, and contributing factors.
        """
        if not self._config.risk_scoring_enabled:
            return RiskAssessment(score=0, level="low", factors=["risk_scoring_disabled"])

        diff_stats = diff_stats or {}
        score = 0
        factors: list[str] = []

        # Check each risk factor
        dep_score = self._check_dependency_files(changed_files)
        if dep_score > 0:
            score += dep_score
            factors.append(f"dependency_file_modified(+{dep_score})")

        cfg_score = self._check_config_files(changed_files)
        if cfg_score > 0:
            score += cfg_score
            factors.append(f"config_file_modified(+{cfg_score})")

        api_score = self._check_api_files(changed_files)
        if api_score > 0:
            score += api_score
            factors.append(f"api_definition_modified(+{api_score})")

        migration_score = self._check_migration_files(changed_files)
        if migration_score > 0:
            score += migration_score
            factors.append(f"database_migration_modified(+{migration_score})")

        diff_score = self._check_diff_size(diff_stats)
        if diff_score > 0:
            score += diff_score
            factors.append(f"large_diff(+{diff_score})")

        delete_score = self._check_deleted_files(diff_stats)
        if delete_score > 0:
            score += delete_score
            factors.append(f"files_deleted(+{delete_score})")

        # Determine risk level
        level = self._score_to_level(score)

        assessment = RiskAssessment(score=score, level=level, factors=factors)
        logger.info(
            "Risk assessment: score=%d, level=%s, factors=%s",
            score, level, factors,
        )
        return assessment

    def get_verification_requirements(self, assessment: RiskAssessment) -> dict:
        """Get verification requirements based on risk level.

        Args:
            assessment: A :class:`RiskAssessment` produced by :meth:`assess_risk`.

        Returns:
            Dict with sensor types as keys and ``bool`` (required) as values.
            Sensor types are ``lint``, ``typecheck``, ``test`` and ``build``.
        """
        if assessment.level == "low":
            return {
                "lint": True,
                "typecheck": False,
                "test": False,
                "build": False,
            }
        elif assessment.level == "medium":
            return {
                "lint": True,
                "typecheck": False,
                "test": True,
                "build": False,
            }
        else:  # high
            return {
                "lint": True,
                "typecheck": True,
                "test": True,
                "build": True,
            }

    # ─── Private risk factor checks ───

    def _check_dependency_files(self, changed_files: list[str]) -> int:
        """Check if any dependency files are modified. Weight: +20."""
        for f in changed_files:
            filename = Path(f).name
            if filename in DEPENDENCY_FILES:
                return 20
        return 0

    def _check_config_files(self, changed_files: list[str]) -> int:
        """Check if config files are modified. Weight: +15."""
        for f in changed_files:
            path = Path(f)
            filename = path.name
            if filename in CONFIG_FILES:
                return 15
            if path.suffix in CONFIG_EXTENSIONS and "test" not in str(path).lower():
                return 15
        return 0

    def _check_api_files(self, changed_files: list[str]) -> int:
        """Check if API/route definitions are modified. Weight: +10."""
        for f in changed_files:
            path_lower = f.lower()
            for indicator in API_INDICATORS:
                if indicator in path_lower:
                    return 10
        return 0

    def _check_migration_files(self, changed_files: list[str]) -> int:
        """Check if database migrations are modified. Weight: +20."""
        for f in changed_files:
            path_lower = f.lower()
            for indicator in MIGRATION_INDICATORS:
                if indicator in path_lower:
                    return 20
        return 0

    def _check_diff_size(self, diff_stats: dict) -> int:
        """Check if the diff is large (> 100 lines). Weight: +10."""
        total_lines = diff_stats.get("lines_added", 0) + diff_stats.get("lines_deleted", 0)
        if total_lines > 100:
            return 10
        return 0

    def _check_deleted_files(self, diff_stats: dict) -> int:
        """Check if files were deleted. Weight: +10."""
        deleted = diff_stats.get("files_deleted", [])
        if deleted:
            return 10
        return 0

    def _score_to_level(self, score: int) -> str:
        """Convert numeric score to risk level string."""
        if score <= 20:
            return "low"
        elif score <= 40:
            return "medium"
        else:
            return "high"


__all__ = ["RiskScorer"]
