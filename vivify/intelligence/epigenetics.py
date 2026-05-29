"""Epigenetics layer - same code, different expression based on environment.

Implements three levels of epigenetic regulation:
1. Probe expression: frequently-hit probes get upregulated (higher frequency/weight),
   long-silent probes get downregulated (lower frequency but never deleted).
2. Environment imprinting: early experiences (within plasticity window) leave
   permanent marks that boost sensitivity to certain signal categories.
3. Plasticity window: high learning rate in early rounds, low (stable) after.

Persisted to ``.vivify/epigenome.json``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Probes that must NEVER be downregulated (security / compliance)
PROTECTED_PROBES = frozenset({
    "secrets_scan",
    "dependency_vulnerabilities",
})


@dataclass
class ProbeExpression:
    """Expression level of a single probe (like gene expression)."""

    probe_id: str
    frequency_multiplier: float = 1.0  # 执行频率倍率 (0.1 - 3.0)
    weight_multiplier: float = 1.0     # 权重倍率 (0.1 - 3.0)
    hit_count: int = 0                 # 总命中次数
    miss_streak: int = 0               # 连续无命中轮次
    last_hit_round: int = 0            # 最后命中的轮次编号

    def upregulate(self, learning_rate: float) -> None:
        """Increase expression (probe found issues)."""
        self.frequency_multiplier = min(3.0, self.frequency_multiplier + 0.1 * learning_rate)
        self.weight_multiplier = min(3.0, self.weight_multiplier + 0.05 * learning_rate)
        self.hit_count += 1
        self.miss_streak = 0

    def downregulate(self, learning_rate: float, min_miss_streak: int = 10) -> None:
        """Decrease expression (probe found nothing).

        Protected probes are never downregulated below 1.0.
        """
        self.miss_streak += 1
        if self.miss_streak >= min_miss_streak:
            decay = 0.02 * learning_rate
            # Protected probes floor at 1.0
            if self.probe_id in PROTECTED_PROBES:
                self.frequency_multiplier = max(1.0, self.frequency_multiplier - decay)
                self.weight_multiplier = max(1.0, self.weight_multiplier - decay * 0.5)
            else:
                self.frequency_multiplier = max(0.1, self.frequency_multiplier - decay)
                self.weight_multiplier = max(0.2, self.weight_multiplier - decay * 0.5)


@dataclass
class EnvironmentImprint:
    """A permanent mark left by early environment experiences."""

    category: str            # 印记类别 (ci_failure, security_alert, test_flake, etc.)
    intensity: float         # 印记强度 (0.0 - 1.0)
    imprinted_at_round: int  # 在第几轮被印记
    description: str = ""

    @property
    def sensitivity_boost(self) -> float:
        """How much extra sensitivity this imprint provides."""
        return self.intensity * 0.5  # 最高 +50% 敏感度


@dataclass
class Epigenome:
    """The complete epigenetic state of a Vivify instance.

    Persisted to .vivify/epigenome.json
    """

    # 探针表达量
    probe_expressions: Dict[str, ProbeExpression] = field(default_factory=dict)
    # 环境印记
    imprints: list = field(default_factory=list)  # List[EnvironmentImprint]
    # 系统状态
    total_rounds: int = 0
    plasticity_window: int = 50          # 前 N 轮为高可塑期
    imprint_threshold: int = 3           # 可塑期内命中 N 次形成印记
    min_miss_streak: int = 10            # 连续无命中 N 轮才开始下调
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    # 遗传来源
    lineage: Optional[str] = None        # 继承自哪个项目的 epigenome

    @property
    def learning_rate(self) -> float:
        """Current learning rate based on plasticity window.

        High during plasticity window, low after stabilization.
        """
        if self.total_rounds <= self.plasticity_window:
            # 高可塑期：学习率 1.0
            return 1.0
        else:
            # 稳定期：学习率随时间衰减到 0.2
            rounds_past = self.total_rounds - self.plasticity_window
            return max(0.2, 1.0 - (rounds_past / 200))

    @property
    def is_plastic(self) -> bool:
        """Whether system is in plasticity window."""
        return self.total_rounds <= self.plasticity_window


class EpigeneticsEngine:
    """Manages the epigenetic layer of a Vivify instance."""

    def __init__(self, vivify_dir: Path, *, plasticity_window: int = 50,
                 imprint_threshold: int = 3, min_miss_streak: int = 10):
        self._epigenome_path = vivify_dir / "epigenome.json"
        self._plasticity_window = plasticity_window
        self._imprint_threshold = imprint_threshold
        self._min_miss_streak = min_miss_streak
        self._epigenome = self._load_or_create()

    @property
    def epigenome(self) -> Epigenome:
        """Expose the epigenome for inspection."""
        return self._epigenome

    def get_probe_multiplier(self, probe_id: str) -> tuple:
        """Get (frequency_multiplier, weight_multiplier) for a probe.

        Returns (1.0, 1.0) for unknown probes.
        """
        expr = self._epigenome.probe_expressions.get(probe_id)
        if expr is None:
            return (1.0, 1.0)

        # Apply imprint sensitivity boosts
        boost = self._get_imprint_boost(probe_id)
        return (
            expr.frequency_multiplier * (1.0 + boost),
            expr.weight_multiplier * (1.0 + boost),
        )

    def record_probe_result(self, probe_id: str, found_issues: bool) -> None:
        """Record that a probe ran and whether it found issues."""
        if probe_id not in self._epigenome.probe_expressions:
            self._epigenome.probe_expressions[probe_id] = ProbeExpression(probe_id=probe_id)

        expr = self._epigenome.probe_expressions[probe_id]
        lr = self._epigenome.learning_rate

        if found_issues:
            expr.upregulate(lr)
            expr.last_hit_round = self._epigenome.total_rounds
            # 在可塑期内命中可以形成印记
            if self._epigenome.is_plastic:
                self._maybe_imprint(probe_id)
        else:
            expr.downregulate(lr, min_miss_streak=self._epigenome.min_miss_streak)

    def advance_round(self) -> None:
        """Called at the end of each kernel loop round."""
        self._epigenome.total_rounds += 1
        self._save()

    def get_status(self) -> dict:
        """Get current epigenetic status for logging."""
        return {
            "total_rounds": self._epigenome.total_rounds,
            "learning_rate": round(self._epigenome.learning_rate, 3),
            "is_plastic": self._epigenome.is_plastic,
            "active_expressions": len(self._epigenome.probe_expressions),
            "imprints": len(self._epigenome.imprints),
        }

    def _maybe_imprint(self, probe_id: str) -> None:
        """During plasticity window, frequent hits leave permanent marks."""
        expr = self._epigenome.probe_expressions[probe_id]
        # 在可塑期内命中 >= threshold 次形成印记
        if expr.hit_count >= self._epigenome.imprint_threshold:
            # 检查是否已有此类印记
            category = self._probe_to_category(probe_id)
            existing = [
                i for i in self._epigenome.imprints
                if isinstance(i, dict) and i.get("category") == category
            ]
            if not existing:
                imprint = {
                    "category": category,
                    "intensity": min(1.0, expr.hit_count * 0.2),
                    "imprinted_at_round": self._epigenome.total_rounds,
                    "description": f"Early sensitivity to {probe_id} issues",
                }
                self._epigenome.imprints.append(imprint)
                logger.info(
                    "Epigenetic imprint formed: category=%s intensity=%.1f round=%d",
                    category, imprint["intensity"], self._epigenome.total_rounds,
                )

    def _get_imprint_boost(self, probe_id: str) -> float:
        """Get sensitivity boost from imprints for this probe."""
        category = self._probe_to_category(probe_id)
        boost = 0.0
        for imprint in self._epigenome.imprints:
            if isinstance(imprint, dict) and imprint.get("category") == category:
                boost += imprint.get("intensity", 0) * 0.5
        return min(0.5, boost)  # Cap at +50%

    def _probe_to_category(self, probe_id: str) -> str:
        """Map probe_id to imprint category."""
        mappings = {
            "ci_status": "ci_failure",
            "lint_typecheck": "code_quality",
            "test_coverage": "testing",
            "dependency_vulnerabilities": "security",
            "secrets_scan": "security",
            "error_log_patterns": "runtime_errors",
            "build_duration": "performance",
            "repo_size": "maintenance",
            "doc_staleness": "documentation",
            "dead_code": "code_quality",
            "stale_branches": "maintenance",
            "github_issue_backlog": "project_health",
        }
        return mappings.get(probe_id, probe_id)

    def _load_or_create(self) -> Epigenome:
        """Load existing epigenome or create new one."""
        if self._epigenome_path.exists():
            try:
                data = json.loads(self._epigenome_path.read_text(encoding="utf-8"))
                return self._deserialize(data)
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Failed to load epigenome.json, creating fresh: %s", e)
        epigenome = Epigenome(
            plasticity_window=self._plasticity_window,
            imprint_threshold=self._imprint_threshold,
            min_miss_streak=self._min_miss_streak,
        )
        return epigenome

    def _save(self) -> None:
        """Persist epigenome to JSON."""
        self._epigenome_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._serialize(self._epigenome)
        self._epigenome_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _serialize(self, epigenome: Epigenome) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "probe_expressions": {
                pid: {
                    "frequency_multiplier": pe.frequency_multiplier,
                    "weight_multiplier": pe.weight_multiplier,
                    "hit_count": pe.hit_count,
                    "miss_streak": pe.miss_streak,
                    "last_hit_round": pe.last_hit_round,
                }
                for pid, pe in epigenome.probe_expressions.items()
            },
            "imprints": epigenome.imprints,
            "total_rounds": epigenome.total_rounds,
            "plasticity_window": epigenome.plasticity_window,
            "imprint_threshold": epigenome.imprint_threshold,
            "min_miss_streak": epigenome.min_miss_streak,
            "created_at": epigenome.created_at,
            "lineage": epigenome.lineage,
        }

    def _deserialize(self, data: dict) -> Epigenome:
        """Deserialize from JSON dict."""
        epigenome = Epigenome()
        epigenome.total_rounds = data.get("total_rounds", 0)
        epigenome.plasticity_window = data.get("plasticity_window", self._plasticity_window)
        epigenome.imprint_threshold = data.get("imprint_threshold", self._imprint_threshold)
        epigenome.min_miss_streak = data.get("min_miss_streak", self._min_miss_streak)
        epigenome.created_at = data.get("created_at", datetime.now().isoformat())
        epigenome.lineage = data.get("lineage")
        epigenome.imprints = data.get("imprints", [])

        for pid, pe_data in data.get("probe_expressions", {}).items():
            epigenome.probe_expressions[pid] = ProbeExpression(
                probe_id=pid,
                frequency_multiplier=pe_data.get("frequency_multiplier", 1.0),
                weight_multiplier=pe_data.get("weight_multiplier", 1.0),
                hit_count=pe_data.get("hit_count", 0),
                miss_streak=pe_data.get("miss_streak", 0),
                last_hit_round=pe_data.get("last_hit_round", 0),
            )

        return epigenome


__all__ = [
    "EpigeneticsEngine",
    "Epigenome",
    "EnvironmentImprint",
    "ProbeExpression",
    "PROTECTED_PROBES",
]
