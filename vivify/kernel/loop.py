"""Main kernel loop — orchestrates the detect → fix → escalate pipeline.

Each ``run_once`` cycle:

1. Run the enabled probes via :func:`vivify.probes.runner.run_probes`.
2. For every Issue:
   * Skip if cooldown / already-escalated / disabled category.
   * Try a direct fixer (no AI). If it lands, log + reset failure counter.
   * Otherwise spawn a worktree, ask the coding agent for a fix, then PR it.
   * Failures bump the FailureTracker; chronic failures escalate to FRs.
3. Pull pending FeatureRequests through :class:`FeaturePipeline`.
4. Optionally run :class:`HealthMonitor` on its own cadence.
5. Detect changes to vivify's own code via :func:`compute_code_hash`; if
   the hash moved, request a graceful restart.

The kernel does **not** know about Qoder CLI specifically — it only depends on
:class:`CodingAgent` and the ``pr_mode`` package.
"""
from __future__ import annotations

import hashlib
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from vivify.agents.history import load_history
from vivify.agents.prompts import builders, parsers
from vivify.fixers.registry import FixerRegistry
from vivify.goals.decomposer import AgentGoalDecomposer, GoalDecomposerConfig
from vivify.goals.parser import parse_goals
from vivify.interfaces.agent import CodingAgent
from vivify.interfaces.fixer import FixContext, FixResult
from vivify.interfaces.goal_decomposer import RepoState
from vivify.interfaces.probe import Probe, ProbeContext
from vivify.interfaces.storage import StorageProvider
from vivify.kernel.code_hash import compute_code_hash
from vivify.kernel.workspace_health import check_workspace_health
from vivify.kernel.dispatch import (
    DispatchPolicy,
    DispatchState,
    mark_attempted,
    select_fixer,
    should_skip,
)
from vivify.kernel.escalator import Escalator, EscalationPolicy
from vivify.kernel.failure_tracker import FailureTracker
from vivify.kernel.feature_pipeline import FeaturePipeline, FeatureRunReport
from vivify.kernel.health_monitor import HealthMonitor
from vivify.models.feature import FeatureRequest, FeatureSpec
from vivify.models.issue import Issue
from vivify.models.snapshot import ActionLog
from vivify.pr_mode.auto_merge import AutoMerge
from vivify.pr_mode.pr_creator import PrCreator
from vivify.pr_mode.quality_check import run_quality_checks
from vivify.pr_mode.self_grow_guard import classify_worktree
from vivify.pr_mode.worktree import WorktreeManager
from vivify.probes.rule_engine import RuleEngine
from vivify.probes.runner import ProbeRunReport, aggregate_issues, run_probes
from vivify.daemon.lock import InstanceLock
from vivify.daemon.manager import DaemonManager  # 仅用于全局实例注册表
from vivify.config.schema import BudgetLimitConfig, CapsuleConfig, DaemonConfig, DeployConfig, GoalsConfig, HarnessConfig, IntelligenceConfig
from vivify.kernel.token_budget import BudgetConfig, P53Suppressor, TokenBucket
from vivify.deployers import DeployResult, get_deployer
from vivify.intelligence.rca import RootCauseAnalyzer
from vivify.intelligence.trend_analyzer import TrendAnalyzer
from vivify.knowledge.maintainer import KnowledgeMaintainer
from vivify.verifier.kpi_snapshot import KpiSnapshotVerifier
from vivify.capsules import CapsuleExtractor, CapsuleStore, SkillCapsule

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────────
# Config + report types
# ────────────────────────────────────────────────────────────────────────────────


@dataclass
class KernelConfig:
    interval_seconds: int = 300
    dry_run: bool = False
    enabled_probe_ids: Optional[set[str]] = None
    per_probe_timeout_seconds: int = 120
    only_category: Optional[str] = None
    max_agent_fixes_per_round: int = 3
    max_features_per_round: int = 3
    enable_self_improve_prompt: bool = False
    repo_url: Optional[str] = None
    package_root: Optional[Path] = None  # for compute_code_hash
    state_dir: str = ".vivify"
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    deploy: DeployConfig = field(default_factory=DeployConfig)
    deploy_url: str = ""  # 部署地址（用于 deploy 后验证）
    goals: GoalsConfig = field(default_factory=GoalsConfig)
    default_branch: str = "main"  # 用于 goal decompose 时构造 RepoState
    rules: list = field(default_factory=list)  # 复合信号规则配置
    qodercli_binary: str = "qodercli"  # qodercli 路径（知识图谱维护用）
    wiki_path: str = ""  # wiki 路径（知识图谱维护用）
    intelligence: IntelligenceConfig = field(default_factory=IntelligenceConfig)
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    budget: BudgetLimitConfig = field(default_factory=BudgetLimitConfig)
    capsules: CapsuleConfig = field(default_factory=CapsuleConfig)


@dataclass
class KernelDeps:
    repo_root: Path
    storage: StorageProvider
    agent: CodingAgent
    probes: list[Probe]
    fixers: FixerRegistry
    worktrees: WorktreeManager
    pr_creator: PrCreator
    auto_merge: Optional[AutoMerge] = None
    health_monitor: Optional[HealthMonitor] = None


@dataclass
class RoundReport:
    run_id: str
    round_num: int
    issues_seen: int = 0
    issues_skipped: int = 0
    direct_fixes: int = 0
    agent_fixes: int = 0
    escalations: int = 0
    features_processed: int = 0
    duration_seconds: float = 0.0
    code_hash: str = ""
    feature_reports: list[FeatureRunReport] = field(default_factory=list)


# ────────────────────────────────────────────────────────────────────────────────
# Kernel
# ────────────────────────────────────────────────────────────────────────────────


class Kernel:
    """Drives the entire vivify loop. One per process."""

    def __init__(
        self,
        *,
        deps: KernelDeps,
        config: KernelConfig | None = None,
        dispatch_policy: DispatchPolicy | None = None,
        escalation_policy: EscalationPolicy | None = None,
    ):
        self.deps = deps
        self.config = config or KernelConfig()
        self.dispatch_policy = dispatch_policy or DispatchPolicy()
        self.escalation_policy = escalation_policy or EscalationPolicy()

        self._dispatch = DispatchState()
        self._tracker = FailureTracker(deps.storage)
        self._escalator = Escalator(
            storage=deps.storage,
            tracker=self._tracker,
            policy=self.escalation_policy,
        )
        self._round_num = 0
        self._initial_code_hash = self._current_code_hash()

        # ── 部署器初始化 ─────────────────────────────────────────
        self._deployer = get_deployer(
            repo_root=deps.repo_root,
            deploy_method=self.config.deploy.method,
            deploy_config=self.config.deploy.model_dump(),
        )

        # ── Goals 自动分解器初始化 ─────────────────────────────────
        _plan_agent = getattr(self.deps.agent, 'cfg', None)
        _plan_for_decompose = getattr(_plan_agent, 'plan_agent_for_decompose', True) if _plan_agent else True
        self._goal_decomposer = AgentGoalDecomposer(
            agent=self.deps.agent,
            repo_root=self.deps.repo_root,
            config=GoalDecomposerConfig(
                max_features_per_decompose=self.config.goals.max_features_per_decompose,
                plan_agent_for_decompose=_plan_for_decompose,
            ),
        )
        self._last_decompose_time: float = 0.0
        self._goals_file_hash: str = ""

        # ── KPI snapshot verifier：每轮采集 feature/probe 指标，为趋势 Tab 提供数据 ────
        try:
            self._kpi_verifier: Optional[KpiSnapshotVerifier] = KpiSnapshotVerifier(
                probes=self.deps.probes,
                storage=self.deps.storage,
                source="kpi_monitor",
            )
        except Exception as e:  # pragma: no cover
            logger.debug("KpiSnapshotVerifier init failed: %s", e)
            self._kpi_verifier = None

        # ── 多实例隔离：获取锁 + 写 PID 文件 ───────────────────────────────
        self._instance_lock: Optional[InstanceLock] = None
        self._pid_file_path: Optional[Path] = None
        self._acquire_instance_lock()

        # ── 知识图谱增量维护器 ─────────────────────────────────────────
        try:
            self._knowledge_maintainer: Optional[KnowledgeMaintainer] = KnowledgeMaintainer(
                project_root=deps.repo_root,
                qodercli_binary=self.config.qodercli_binary,
                wiki_path=self.config.wiki_path,
            )
        except Exception as e:  # pragma: no cover
            logger.debug("KnowledgeMaintainer init failed: %s", e)
            self._knowledge_maintainer = None

        # ── 智能分析（RCA + 趋势） ─────────────────────────────
        try:
            self._rca: Optional[RootCauseAnalyzer] = RootCauseAnalyzer(
                storage=self.deps.storage,
                rca_threshold=self.config.intelligence.rca_recurrence_threshold,
            )
        except Exception as e:  # pragma: no cover
            logger.debug("RootCauseAnalyzer init failed: %s", e)
            self._rca = None
        try:
            self._trend_analyzer: Optional[TrendAnalyzer] = TrendAnalyzer(
                storage=self.deps.storage,
                window_days=self.config.intelligence.trend_window_days,
            )
        except Exception as e:  # pragma: no cover
            logger.debug("TrendAnalyzer init failed: %s", e)
            self._trend_analyzer = None
        self._trend_round_counter: int = 0
        self._latest_health_summary = None
        # 传递 RCA 上下文到 _try_agent_fix：issue.hash -> formatted prompt context
        self._rca_contexts: dict[str, str] = {}

        # ── Token Budget + P53 Suppressor ───────────────────────────────
        _budget_cfg = BudgetConfig(
            daily_limit=self.config.budget.daily_limit,
            per_cycle_limit=self.config.budget.per_cycle_limit,
            window_seconds=self.config.budget.window_seconds,
            pr_frequency_threshold=self.config.budget.pr_frequency_threshold,
            backlog_threshold=self.config.budget.backlog_threshold,
            cooldown_multiplier=self.config.budget.cooldown_multiplier,
        )
        self._token_bucket = TokenBucket(config=_budget_cfg)
        self._p53 = P53Suppressor(config=_budget_cfg)

        # ── Harness (PEV) 初始化：sensors / doom-loop / risk / guides ───────
        self._sensor_engine = None
        self._doom_detector = None
        self._risk_scorer = None
        self._guides_manager = None
        self._init_harness()

        # ── Skill Capsule store + extractor ───────────────────────────────
        self._capsule_store: Optional[CapsuleStore] = None
        self._capsule_extractor: Optional[CapsuleExtractor] = None
        self._init_capsules()

        # ── 信号处理：SIGTERM / SIGINT 触发优雅停止 ──────────────────────
        self._shutdown_requested = False
        try:
            signal.signal(signal.SIGTERM, self._handle_shutdown)
            signal.signal(signal.SIGINT, self._handle_shutdown)
        except (ValueError, OSError) as e:
            # 仅在主线程能注册信号；非主线程（如测试）静默跳过
            logger.debug("Signal handlers not registered: %s", e)

    # ── public API ─────────────────────────────────────────────────────────
    def run_forever(self, *, max_rounds: Optional[int] = None) -> None:
        """Loop ``run_once`` on ``interval_seconds`` until shutdown / hash change."""
        try:
            while not self._shutdown_requested:
                report = self.run_once()
                if max_rounds is not None and report.round_num >= max_rounds:
                    break
                if self._code_hash_changed(report.code_hash):
                    logger.warning(
                        "vivify package hash changed (%s → %s); requesting restart",
                        self._initial_code_hash[:12], report.code_hash[:12],
                    )
                    break
                # 分段 sleep 以便快速响应信号
                self._interruptible_sleep(max(1, self.config.interval_seconds))
            logger.info("Shutdown complete.")
        finally:
            self._release_instance_lock()

    # ── daemon lifecycle helpers ───────────────────────────────────────────
    def _acquire_instance_lock(self) -> None:
        """Acquire a per-project lock; raise if another instance owns it."""
        state_dir = Path(self.config.state_dir)
        if not state_dir.is_absolute():
            state_dir = self.deps.repo_root / state_dir
        state_dir.mkdir(parents=True, exist_ok=True)

        lock_path = state_dir / self.config.daemon.lock_file
        lock = InstanceLock(lock_path)
        if not lock.acquire():
            raise RuntimeError(
                f"Another vivify instance is already running for "
                f"{self.deps.repo_root} (lock: {lock_path})"
            )
        self._instance_lock = lock

        # 写入 PID 文件
        pid_path = state_dir / self.config.daemon.pid_file
        try:
            pid_path.write_text(str(os.getpid()), encoding="utf-8")
            self._pid_file_path = pid_path
        except OSError as e:
            logger.warning("Failed to write PID file %s: %s", pid_path, e)

        # 注册到全局实例注册表（best-effort，便于 list-instances）
        try:
            DaemonManager(self.deps.repo_root, state_dir)._register_instance(os.getpid())
        except Exception as e:  # pragma: no cover — registry is best-effort
            logger.debug("Global registry update skipped: %s", e)

    def _handle_shutdown(self, signum, frame):
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        logger.info("Received signal %s, requesting graceful shutdown...", signum)
        self._shutdown_requested = True

    # ── Harness (PEV) helpers ─────────────────────────────────────────────────
    def _init_harness(self) -> None:
        """Initialise harness components when ``config.harness.enabled``.

        Failures are logged and silently ignored so the kernel still works
        when the harness sub-package is missing or misconfigured.
        """
        harness_cfg = getattr(self.config, "harness", None)
        if harness_cfg is None or not getattr(harness_cfg, "enabled", False):
            return
        try:
            from vivify.harness.sensors import HarnessSensorEngine
            from vivify.harness.doom_loop import DoomLoopDetector
            from vivify.harness.risk_scorer import RiskScorer
            from vivify.harness.guides import GuidesManager
        except Exception as e:  # pragma: no cover
            logger.debug("Harness import failed (disabled): %s", e)
            return
        try:
            self._sensor_engine = HarnessSensorEngine(harness_cfg, self.deps.repo_root)
            self._doom_detector = DoomLoopDetector(
                window_size=harness_cfg.doom_loop_window,
                threshold=harness_cfg.doom_loop_threshold,
            )
            self._risk_scorer = RiskScorer(harness_cfg)
            guides_dir = Path(harness_cfg.guides_dir)
            if not guides_dir.is_absolute():
                guides_dir = self.deps.repo_root / guides_dir
            self._guides_manager = GuidesManager(guides_dir)
            logger.info("Harness initialised (guides_dir=%s)", guides_dir)
        except Exception as e:  # pragma: no cover
            logger.warning("Harness init failed: %s", e)
            self._sensor_engine = None
            self._doom_detector = None
            self._risk_scorer = None
            self._guides_manager = None

    def _harness_enabled(self) -> bool:
        return (
            getattr(self.config, "harness", None) is not None
            and self.config.harness.enabled
            and self._sensor_engine is not None
        )

    # ── Skill Capsule helpers ────────────────────────────────────────
    def _init_capsules(self) -> None:
        """Initialise the skill-capsule store + extractor (best-effort)."""
        cfg = getattr(self.config, "capsules", None)
        if cfg is None or not getattr(cfg, "enabled", False):
            return
        try:
            capsules_dir = Path(cfg.capsules_dir)
            if not capsules_dir.is_absolute():
                capsules_dir = self.deps.repo_root / capsules_dir
            self._capsule_store = CapsuleStore(capsules_dir)
            self._capsule_extractor = CapsuleExtractor()
            logger.debug("Skill capsule store ready at %s", capsules_dir)
        except Exception as e:  # pragma: no cover
            logger.warning("Capsule subsystem init failed: %s", e)
            self._capsule_store = None
            self._capsule_extractor = None

    def _capsules_enabled(self) -> bool:
        return self._capsule_store is not None and self._capsule_extractor is not None

    def _lookup_capsule_hint(self, issue: Issue) -> tuple[str, Optional[SkillCapsule]]:
        """Return the prompt hint + matching capsule for an issue, if any."""
        if not self._capsules_enabled():
            return "", None
        try:
            issue_text = " ".join(
                [issue.title or "", issue.description or "", issue.category or ""]
            )
            cap = self._capsule_store.find_matching(issue.source_probe, issue_text)
            if cap is None:
                return "", None
            return cap.prompt_template, cap
        except Exception as e:  # pragma: no cover
            logger.debug("capsule lookup failed: %s", e)
            return "", None

    def _record_capsule_outcome(self, capsule: Optional[SkillCapsule], success: bool) -> None:
        if capsule is None or not self._capsules_enabled():
            return
        try:
            self._capsule_store.record_usage(capsule.capsule_id, success)
        except Exception as e:  # pragma: no cover
            logger.debug("capsule record_usage failed: %s", e)

    def _maybe_extract_capsule(
        self,
        *,
        run_id: str,
        issue: Issue,
        output: str,
        pr_url: Optional[str],
        commit_hash: Optional[str],
        existing_capsule: Optional[SkillCapsule],
        wt_path: Optional[Path] = None,
        base_ref: Optional[str] = None,
    ) -> None:
        """Distil a new capsule from a successful agent fix.

        Skipped when an existing capsule was already used (its usage counter
        is enough), when the subsystem is disabled, or on any failure.
        """
        if not self._capsules_enabled() or existing_capsule is not None:
            return
        try:
            action_log = {
                "run_id": run_id,
                "action_type": "heal",
                "status": "success",
                "category": issue.category,
                "title": issue.title,
                "result_summary": (output or "")[-1000:],
                "details": {
                    "source_probe": issue.source_probe,
                    "pr_url": pr_url,
                    "commit_hash": commit_hash,
                },
            }
            issue_dict = issue.to_dict() if hasattr(issue, "to_dict") else {
                "title": issue.title,
                "description": issue.description,
                "category": issue.category,
                "source_probe": issue.source_probe,
            }
            diff_text = ""
            if wt_path is not None and base_ref:
                try:
                    import subprocess
                    res = subprocess.run(
                        ["git", "diff", base_ref, "HEAD"],
                        cwd=str(wt_path), capture_output=True, text=True, timeout=15,
                    )
                    if res.returncode == 0:
                        diff_text = res.stdout[:20000]
                except Exception:  # pragma: no cover
                    diff_text = ""
            cap = self._capsule_extractor.extract_from_fix(
                action_log, issue_dict, diff_text,
            )
            self._capsule_store.save(cap)
            logger.info(
                "Skill capsule extracted: id=%s probe=%s category=%s",
                cap.capsule_id[:8], cap.probe_id, cap.issue_category,
            )
        except Exception as e:  # pragma: no cover
            logger.debug("capsule extraction failed: %s", e)

    def _get_changed_files(self, worktree_path: Path, base_ref: str) -> list[str]:
        """Return the list of files changed in the worktree relative to ``base_ref``.

        Falls back to an empty list on failure.
        """
        try:
            import subprocess
            res = subprocess.run(
                ["git", "diff", "--name-only", base_ref, "HEAD"],
                cwd=str(worktree_path),
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode != 0:
                return []
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]
        except Exception as e:  # pragma: no cover
            logger.debug("_get_changed_files failed: %s", e)
            return []

    def _get_diff_stats(self, worktree_path: Path, base_ref: str) -> dict:
        """Return diff statistics for risk scoring."""
        stats: dict = {"lines_added": 0, "lines_deleted": 0, "files_deleted": []}
        try:
            import subprocess
            res = subprocess.run(
                ["git", "diff", "--numstat", base_ref, "HEAD"],
                cwd=str(worktree_path),
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        try:
                            stats["lines_added"] += int(parts[0]) if parts[0] != "-" else 0
                            stats["lines_deleted"] += int(parts[1]) if parts[1] != "-" else 0
                        except (ValueError, TypeError):
                            pass
            res2 = subprocess.run(
                ["git", "diff", "--diff-filter=D", "--name-only", base_ref, "HEAD"],
                cwd=str(worktree_path),
                capture_output=True, text=True, timeout=15,
            )
            if res2.returncode == 0:
                stats["files_deleted"] = [
                    line.strip() for line in res2.stdout.splitlines() if line.strip()
                ]
        except Exception as e:  # pragma: no cover
            logger.debug("_get_diff_stats failed: %s", e)
        return stats

    def _run_harness_verification(
        self,
        issue: Issue,
        wt,
        prompt: str,
        max_turns: int,
        category: str,
    ) -> tuple[bool, str]:
        """Run sensors after a fix and retry agent with feedback on failures.

        Returns a tuple ``(passed, last_agent_output)``. ``passed`` reports
        whether sensors eventually passed. The caller is responsible for
        deciding whether to proceed with PR creation.
        """
        if not self._harness_enabled():
            return True, ""
        harness_cfg = self.config.harness
        try:
            changed_files = self._get_changed_files(wt.path, wt.base_ref)
            diff_stats = self._get_diff_stats(wt.path, wt.base_ref)
            risk = (
                self._risk_scorer.assess_risk(changed_files, diff_stats)
                if self._risk_scorer is not None
                else None
            )
            report = self._sensor_engine.run_all_sensors(changed_files=changed_files)
            if risk is not None:
                report.risk_level = risk.level
            if report.all_passed:
                logger.info(
                    "Harness verification passed (risk=%s)",
                    getattr(risk, "level", "?"),
                )
                return True, ""

            last_output = ""
            for retry in range(max(0, harness_cfg.max_feedback_retries)):
                logger.info(
                    "Harness retry %d/%d for %s",
                    retry + 1, harness_cfg.max_feedback_retries, issue.hash,
                )
                feedback_prompt = f"{prompt}\n\n{report.feedback_prompt}"
                try:
                    agent_result = self.deps.agent.heal(
                        feedback_prompt,
                        max_turns=max_turns,
                        category=category,
                        workspace=wt.path,
                    )
                    last_output = agent_result.output or ""
                except Exception as e:  # pragma: no cover
                    logger.warning("Harness retry agent.heal failed: %s", e)
                    break
                changed_files = self._get_changed_files(wt.path, wt.base_ref)
                report = self._sensor_engine.run_all_sensors(changed_files=changed_files)
                if report.all_passed:
                    logger.info(
                        "Harness verification passed on retry %d", retry + 1,
                    )
                    return True, last_output
            logger.warning("Harness verification failed after all retries")
            return False, last_output
        except Exception as e:  # pragma: no cover
            logger.warning("Harness verification crashed (treated as pass): %s", e)
            return True, ""

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in small chunks so shutdown signals are handled quickly."""
        end = time.time() + seconds
        while time.time() < end and not self._shutdown_requested:
            remaining = end - time.time()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))

    def _release_instance_lock(self) -> None:
        """Release lock, clean up PID file, and unregister instance."""
        if getattr(self, "_instance_lock", None) is not None:
            try:
                self._instance_lock.release()
            except Exception as e:  # pragma: no cover
                logger.debug("Lock release failed: %s", e)
            self._instance_lock = None

        pid_path = getattr(self, "_pid_file_path", None)
        if pid_path is not None:
            try:
                pid_path.unlink(missing_ok=True)
            except OSError as e:  # pragma: no cover
                logger.debug("PID file cleanup failed: %s", e)
            self._pid_file_path = None

        # 从全局实例注册表移除（best-effort）
        try:
            state_dir = Path(self.config.state_dir)
            if not state_dir.is_absolute():
                state_dir = self.deps.repo_root / state_dir
            DaemonManager(self.deps.repo_root, state_dir)._unregister_instance()
        except Exception as e:  # pragma: no cover
            logger.debug("Global registry cleanup skipped: %s", e)

    def run_once(self) -> RoundReport:
        self._round_num += 1
        run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        report = RoundReport(run_id=run_id, round_num=self._round_num)
        t0 = time.time()

        # ── Token budget: reset per-cycle counter ─────────────────────
        self._token_bucket.reset_cycle()

        # ── P53 check: if suppressed, skip entire round ───────────────
        if self._token_bucket.is_suppressed:
            logger.warning(
                "p53 suppression active, skipping round %d: %s",
                self._round_num, self._token_bucket.suppression_reason,
            )
            report.duration_seconds = time.time() - t0
            report.code_hash = self._current_code_hash()
            return report

        try:
            # Knowledge graph incremental maintenance (rate-limited, non-blocking)
            self._maybe_update_knowledge()

            issues, probe_reports = self._detect()
            report.issues_seen = len(issues)
            self._evaluate_rules(probe_reports, report=report)
            self._maybe_run_rca(issues)
            self._handle_issues(issues, report=report)
            self._maybe_decompose_goals()
            self._handle_features(report=report)
            self._maybe_run_health_monitor(report=report)
            self._maybe_capture_kpi_snapshot()
            self._maybe_run_trend_analysis()
        except Exception as e:
            logger.exception("Kernel round failed: %s", e)
            report.duration_seconds = time.time() - t0
        finally:
            report.duration_seconds = report.duration_seconds or (time.time() - t0)
            report.code_hash = self._current_code_hash()

        # ── P53 evaluation at end of round ────────────────────────────
        self._evaluate_p53()

        # ── Log budget usage ──────────────────────────────────────────
        logger.info("Token budget: %s", self._token_bucket.usage_report)

        return report

    # ── p53 evaluation ─────────────────────────────────────────────────────
    def _evaluate_p53(self) -> None:
        """Collect proliferation metrics and evaluate p53 suppression."""
        try:
            metrics: dict = {
                "pr_count_24h": 0,
                "pending_features": 0,
                "failed_fixes_24h": 0,
            }
            # Count recent PRs and failures from action logs
            try:
                logs = self.deps.storage.list_action_logs(limit=200)
                pr_count = sum(
                    1 for log in logs
                    if getattr(log, 'action_type', '') == 'heal'
                    and getattr(log, 'status', '') == 'success'
                    and getattr(log, 'pr_url', None)
                )
                failed_count = sum(
                    1 for log in logs
                    if getattr(log, 'action_type', '') == 'heal'
                    and getattr(log, 'status', '') == 'failed'
                )
                metrics["pr_count_24h"] = pr_count
                metrics["failed_fixes_24h"] = failed_count
            except Exception:  # pragma: no cover
                pass
            # Count pending features
            try:
                pending: list = []
                for status in ("pending", "approved"):
                    pending.extend(self.deps.storage.list_features(status=status, limit=100))
                metrics["pending_features"] = len(pending)
            except Exception:  # pragma: no cover
                pass

            reason = self._p53.evaluate(metrics)
            if reason:
                self._token_bucket.suppress(reason)
                logger.warning("p53 suppression activated: %s", reason)
        except Exception as e:  # pragma: no cover
            logger.debug("p53 evaluation failed: %s", e)

    # ── stage 0: knowledge maintenance ─────────────────────────────────────
    def _maybe_update_knowledge(self) -> None:
        """Invoke knowledge graph incremental maintenance. Never raises."""
        if self._knowledge_maintainer is None:
            return
        try:
            self._knowledge_maintainer.maybe_update()
        except Exception as e:  # pragma: no cover
            logger.debug("Knowledge maintenance error: %s", e)

    # ── stage 1: detect ────────────────────────────────────────────────────
    def _detect(self) -> tuple[list[Issue], list[ProbeRunReport]]:
        ctx = ProbeContext(
            repo_root=self.deps.repo_root,
            config=None,        # type: ignore[arg-type] — kernel does not depend on schema
            storage=self.deps.storage,
            logger=logger.getChild("probe"),
        )
        reports = run_probes(
            self.deps.probes, ctx,
            per_probe_timeout_seconds=self.config.per_probe_timeout_seconds,
            enabled_ids=self.config.enabled_probe_ids,
        )
        return aggregate_issues(reports), reports

    # ── stage 1.5: rule engine evaluation ──────────────────────────────────
    def _evaluate_rules(
        self, probe_reports: list[ProbeRunReport], *, report: RoundReport
    ) -> None:
        """评估复合规则；无规则配置时零开销跳过。"""
        if not self.config.rules:
            return
        # 收集 probe raw_data: {probe_id: raw_dict}
        probe_results: dict[str, dict] = {}
        for pr in probe_reports:
            if pr.raw_data:
                probe_results[pr.probe_id] = pr.raw_data
        if not probe_results:
            return

        engine = RuleEngine(self.config.rules)
        evaluations = engine.evaluate(probe_results)
        for ev in evaluations:
            if ev.triggered:
                self._handle_rule_action(ev, report=report)

    def _handle_rule_action(self, ev, *, report: RoundReport) -> None:
        """根据触发的规则执行对应 action。"""
        from vivify.probes.rule_engine import RuleEvaluation

        rule = ev.rule
        logger.info(
            "Rule triggered: [%s] %s — action=%s severity=%s",
            rule.name, rule.message or '(no message)', rule.action, rule.severity,
        )
        if rule.action == "create_feature":
            try:
                fr = FeatureRequest(
                    title=f"[rule:{rule.name}] {rule.message}"[:200],
                    description=(
                        f"Triggered by composite rule '{rule.name}'.\n"
                        f"Matched conditions: {', '.join(ev.matched_conditions)}\n"
                        f"Severity: {rule.severity}"
                    ),
                    type="improvement",
                    priority="P1" if rule.severity in ("high", "critical") else "P2",
                )
                self.deps.storage.create_feature(fr)
            except Exception as e:  # pragma: no cover
                logger.warning("Rule action create_feature failed: %s", e)
        elif rule.action == "escalate":
            logger.warning(
                "Rule escalation: %s — %s", rule.name, rule.message,
            )
        else:
            # Default: create_issue — log as action for visibility
            try:
                self.deps.storage.log_action(
                    ActionLog(
                        run_id=report.run_id,
                        round_num=self._round_num,
                        action_type="rule_triggered",
                        status="triggered",
                        category="rule_engine",
                        title=f"[{rule.name}] {rule.message}"[:200],
                        result_summary=(
                            f"Matched: {', '.join(ev.matched_conditions)}"
                        )[:2000],
                        details={
                            "rule_name": rule.name,
                            "severity": rule.severity,
                            "action": rule.action,
                            "matched_conditions": ev.matched_conditions,
                        },
                    )
                )
            except Exception as e:  # pragma: no cover
                logger.debug("Rule action log failed: %s", e)

    # ── stage 1.6: RCA pre-fix enrichment ────────────────────────────
    def _maybe_run_rca(self, issues: list[Issue]) -> None:
        """对检测到的 issues 进行聚类 + 重复根因分析。

        失败不影响主循环；RCA 上下文会被暂存到 ``self._rca_contexts``，
        供后续 _try_agent_fix 读取并拼接到 fix prompt。
        """
        # 清理上一轮的上下文，避免跨轮泄漏
        self._rca_contexts.clear()
        if not issues or self._rca is None:
            return
        if not self.config.intelligence.rca_enabled:
            return
        try:
            # Issue 聚类（仅记录日志，供后续可观测性使用）
            try:
                clusters = self._rca.group_similar_issues(issues)
                if len(clusters) < len(issues):
                    logger.info(
                        "RCA clustered %d issues into %d groups",
                        len(issues), len(clusters),
                    )
            except Exception as e:  # pragma: no cover
                logger.debug("Issue clustering failed: %s", e)

            # 对重复 issue 触发 RCA
            rca_count = 0
            max_per_round = max(0, self.config.intelligence.rca_max_per_round)
            for issue in issues:
                if rca_count >= max_per_round:
                    break
                try:
                    rca_report = self._rca.analyze_recurrence(issue)
                except Exception as e:  # pragma: no cover
                    logger.debug("analyze_recurrence(%s) failed: %s", issue.hash, e)
                    continue
                if rca_report is None:
                    continue
                try:
                    self._rca_contexts[issue.hash] = self._rca.format_rca_context(rca_report)
                except Exception as e:  # pragma: no cover
                    logger.debug("format_rca_context failed: %s", e)
                    continue
                rca_count += 1
                logger.info(
                    "RCA generated for %s (recurrence=%d)",
                    issue.hash, rca_report.recurrence_count,
                )
        except Exception as e:
            logger.warning("RCA analysis failed: %s", e)

    # ── stage 2: handle issues ─────────────────────────────────────────────
    def _handle_issues(self, issues: list[Issue], *, report: RoundReport) -> None:
        agent_budget = self.config.max_agent_fixes_per_round
        seen_hashes = {i.hash for i in issues}
        for issue in issues:
            if self.config.only_category and issue.category != self.config.only_category:
                report.issues_skipped += 1
                continue

            upgraded = self._tracker.already_upgraded(issue.hash)
            reason = should_skip(
                issue, state=self._dispatch,
                policy=self.dispatch_policy, upgraded=upgraded,
            )
            if reason:
                report.issues_skipped += 1
                logger.info("skip %s/%s: %s", issue.category, issue.hash, reason)
                continue

            if self.config.dry_run:
                self._log_issue_action(report.run_id, "detect", "success", issue,
                                       summary="dry-run: no action taken")
                continue

            # Try direct fixer first.
            fix_ctx = FixContext(
                repo_root=self.deps.repo_root,
                config=None,    # type: ignore[arg-type]
                storage=self.deps.storage,
                logger=logger.getChild("fixer"),
            )
            fixer = select_fixer(issue, registry=self.deps.fixers, ctx=fix_ctx)
            if fixer is not None:
                if self._try_direct_fix(issue, fixer, fix_ctx, report=report):
                    self._tracker.reset(issue.hash)
                    continue
                # Fall-through to agent if direct fix didn't land.

            # Coding agent path.
            if agent_budget <= 0:
                logger.info("agent budget exhausted; deferring %s", issue.hash)
                report.issues_skipped += 1
                continue
            # Token budget gate: check before agent call
            if not self._token_bucket.can_consume():
                logger.info("token budget exhausted; deferring %s", issue.hash)
                report.issues_skipped += 1
                continue
            if self._try_agent_fix(issue, report=report):
                self._token_bucket.consume()
                self._tracker.reset(issue.hash)
                agent_budget -= 1
            else:
                # Bump failure counter + maybe escalate.
                fail_count = self._tracker.record(issue)
                logger.info("agent fix failed; fail_count=%s", fail_count)
                fid = self._escalator.maybe_escalate(issue)
                if fid:
                    report.escalations += 1

            mark_attempted(self._dispatch, issue)

        # Reset counters for hashes that no longer appear.
        known = set(self._dispatch.fail_counts.keys())
        self._tracker.reset_resolved(seen_hashes, known)

    def _try_direct_fix(
        self,
        issue: Issue,
        fixer,
        ctx: FixContext,
        *,
        report: RoundReport,
    ) -> bool:
        t0 = time.time()
        try:
            result: FixResult = fixer.fix(issue, ctx)
        except Exception as e:
            logger.exception("Fixer %s.fix raised: %s", fixer.id, e)
            return False
        success = bool(result.fixed)
        report.direct_fixes += int(success)
        self._log_issue_action(
            report.run_id, "direct_fix", "success" if success else "failed",
            issue,
            summary=result.message[:1000] if result.message else "",
            duration=time.time() - t0,
            details={"fixer_id": fixer.id,
                     "changed_files": result.changed_files,
                     "pr_url": result.pr_url},
            pr_url=result.pr_url, commit_hash=result.commit_hash,
        )
        return success

    def _try_agent_fix(self, issue: Issue, *, report: RoundReport) -> bool:
        # Pre-flight workspace health check: abort early if workspace is unhealthy.
        health = check_workspace_health(self.deps.repo_root)
        if not health.passed:
            logger.warning(
                "Pre-flight check failed for %s: %s", issue.hash, health.summary,
            )
            self._log_issue_action(
                report.run_id, "heal", "skipped", issue,
                summary=f"pre-flight failed: {health.summary}",
            )
            return False

        # Doom-loop pre-check: skip when the same fingerprint repeats too often.
        if self._harness_enabled() and self._doom_detector is not None:
            try:
                self._doom_detector.record_action(
                    category=issue.category,
                    issue_hash=issue.hash,
                    action_type="agent_fix",
                )
                if self._doom_detector.is_looping():
                    escape = self._doom_detector.get_escape_strategy()
                    logger.warning(
                        "Doom-loop detected for %s, skipping. %s",
                        issue.hash, escape.splitlines()[0] if escape else "",
                    )
                    self._log_issue_action(
                        report.run_id, "heal", "skipped", issue,
                        summary="doom-loop detected; skipped agent fix",
                    )
                    return False
            except Exception as e:  # pragma: no cover
                logger.debug("Doom-loop check failed: %s", e)

        slug = f"{issue.category}-{issue.hash}"
        wt = self.deps.worktrees.create(slug)
        t0 = time.time()
        capsule_hint, matched_capsule = self._lookup_capsule_hint(issue)
        if matched_capsule is not None:
            logger.info(
                "Skill capsule fast-path applied: id=%s effectiveness=%.2f",
                matched_capsule.capsule_id[:8], matched_capsule.effectiveness,
            )
        try:
            history = load_history(self.deps.storage, "fix_issue")
            rca_hint = self._rca_contexts.get(issue.hash, "")
            prompt = builders.build_fix_issue(
                issue, workspace=str(wt.path),
                recent_history=history,
                remediation_hint=rca_hint,
                enable_self_improve=self.config.enable_self_improve_prompt,
                capsule_hint=capsule_hint,
            )
            agent_result = self.deps.agent.heal(
                prompt,
                max_turns=30, category="fix_issue",
                workspace=wt.path,
            )
            output = agent_result.output or ""

            # PEV verification: run sensors after fix; retry with feedback on failure.
            if self._harness_enabled():
                passed, retry_output = self._run_harness_verification(
                    issue=issue,
                    wt=wt,
                    prompt=prompt,
                    max_turns=30,
                    category="fix_issue",
                )
                if retry_output:
                    output = retry_output
                if not passed:
                    self._log_issue_action(
                        report.run_id, "heal", "failed", issue,
                        summary="harness verification failed after retries",
                        duration=time.time() - t0,
                    )
                    self._record_capsule_outcome(matched_capsule, success=False)
                    return False

            quality = run_quality_checks(wt.path, base_ref=wt.base_ref)
            if not quality.passed:
                self._log_issue_action(
                    report.run_id, "heal", "failed", issue,
                    summary=f"quality failed: {quality.summary}",
                    duration=time.time() - t0,
                )
                self._record_capsule_outcome(matched_capsule, success=False)
                return False

            decision = classify_worktree(wt.path, base_ref=wt.base_ref)
            commit = parsers.parse_commit_info(output, repo_url=self.config.repo_url)
            pr = self.deps.pr_creator.push_and_open(
                wt,
                title=f"vivify: {issue.title}"[:200],
                body=self._render_issue_pr_body(issue, output=output),
                decision=decision,
            )
            if self.deps.auto_merge:
                merge_outcome = self.deps.auto_merge.try_merge(pr, decision=decision, cwd=wt.path)
            else:
                merge_outcome = None

            # PR 合并后标记知识图谱需要更新
            if merge_outcome and merge_outcome.merged and self._knowledge_maintainer:
                self._knowledge_maintainer.mark_update_needed()

            # 仅在 PR 实际合并后触发部署
            if self._deployer and self.config.deploy.enabled:
                if merge_outcome and merge_outcome.merged:
                    logger.info("PR merged, executing deploy...")
                    self._execute_deploy(report)
                elif merge_outcome and merge_outcome.requested and not merge_outcome.merged:
                    logger.info("Auto-merge requested but not yet merged (timeout); deploy skipped")
                elif not self.deps.auto_merge:
                    # 无 auto_merge 配置（手动合并场景），跳过部署
                    logger.info("No auto_merge configured; deploy skipped until next run")

            report.agent_fixes += 1
            self._log_issue_action(
                report.run_id, "heal", "success", issue,
                summary=output[-1000:] if output else "",
                details={"pr_url": pr.url, "labels": list(pr.labels)},
                duration=time.time() - t0,
                pr_url=pr.url, commit_hash=commit.get("commit_hash"),
            )
            # Skill capsule bookkeeping: record usage and/or distil a new one.
            self._record_capsule_outcome(matched_capsule, success=True)
            self._maybe_extract_capsule(
                run_id=report.run_id,
                issue=issue,
                output=output,
                pr_url=pr.url,
                commit_hash=commit.get("commit_hash"),
                existing_capsule=matched_capsule,
                wt_path=wt.path,
                base_ref=wt.base_ref,
            )
            return True
        except Exception as e:
            logger.exception("agent fix failed for %s: %s", issue.hash, e)
            self._log_issue_action(
                report.run_id, "heal", "failed", issue,
                summary=f"exception: {e!r}",
                duration=time.time() - t0,
            )
            self._record_capsule_outcome(matched_capsule, success=False)
            return False
        finally:
            try:
                self.deps.worktrees.remove(wt)
            except Exception as e:  # pragma: no cover
                logger.warning("worktree cleanup failed: %s", e)

    # ── deploy ─────────────────────────────────────────────────────────────

    def _execute_deploy(self, report: RoundReport) -> None:
        """PR 合并后执行自动部署"""
        logger.info("Starting deployment (method: %s)", self.config.deploy.method)
        try:
            result = self._deployer.deploy()  # type: ignore[union-attr]

            if result.success:
                logger.info(
                    "Deploy succeeded: %s (%.1fs)",
                    result.message, result.duration_seconds,
                )
                # 部署后验证
                if self.config.deploy.verify_after_deploy and self.config.deploy_url:
                    verified = self._deployer.verify(self.config.deploy_url)  # type: ignore[union-attr]
                    result.verified = verified
                    if verified:
                        logger.info(
                            "Post-deploy verification passed: %s",
                            self.config.deploy_url,
                        )
                    else:
                        logger.warning(
                            "Post-deploy verification failed: %s",
                            self.config.deploy_url,
                        )
            else:
                logger.error("Deploy failed: %s", result.error)

            self._log_deploy(result, report)

        except Exception as e:
            logger.error("Deploy exception: %s", e)
            self._log_deploy(
                DeployResult(
                    success=False,
                    method=self.config.deploy.method,
                    error=str(e),
                ),
                report,
            )

    def _log_deploy(self, result: DeployResult, report: RoundReport) -> None:
        """将部署结果记录到 action_logs"""
        try:
            self.deps.storage.log_action(
                ActionLog(
                    run_id=report.run_id,
                    round_num=self._round_num,
                    action_type="deploy",
                    status="success" if result.success else "failed",
                    category="deploy",
                    title=f"deploy via {result.method}",
                    result_summary=(
                        result.message if result.success else result.error
                    )[:2000],
                    duration_seconds=result.duration_seconds,
                    details={
                        "method": result.method,
                        "deploy_url": result.deploy_url,
                        "verified": result.verified,
                    },
                )
            )
        except Exception as e:  # pragma: no cover
            logger.debug("log_action(deploy) failed: %s", e)

    # ── stage 3: feature pipeline ──────────────────────────────────────────

    _PRIORITY_RANK = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}

    def _handle_features(self, *, report: RoundReport) -> None:
        if self.config.dry_run:
            return
        pipeline = FeaturePipeline(
            agent=self.deps.agent,
            storage=self.deps.storage,
            worktree_mgr=self.deps.worktrees,
            pr_creator=self.deps.pr_creator,
            auto_merge=self.deps.auto_merge,
            run_id=report.run_id,
        )
        # Recover any features stuck in transient states from prior rounds
        # before pulling fresh work; some may flip back into ``pending`` /
        # ``approved`` / ``deployed`` and become eligible again this round.
        try:
            pipeline._detect_and_recover_timeouts()
        except Exception as e:  # pragma: no cover
            logger.warning("feature timeout recovery failed: %s", e)

        # Fix #69: recover features stuck in deployed_with_issues due to PR failures
        try:
            pipeline._recover_failed_deployments()
        except Exception as e:  # pragma: no cover
            logger.warning("feature deployment recovery failed: %s", e)

        pending = []
        for status in ("pending", "approved"):
            try:
                pending.extend(self.deps.storage.list_features(status=status, limit=50))
            except Exception as e:  # pragma: no cover
                logger.debug("list_features(%s) failed: %s", status, e)
        if not pending:
            return

        # Sort by priority: P0 > P1 > P2 > P3 > None; parent before followup; then by id.
        pending = sorted(
            pending,
            key=lambda f: (
                -self._PRIORITY_RANK.get(getattr(f, 'priority', None) or '', 0),
                getattr(f, 'parent_id', None) or 0,  # parent (0) before followup
                f.id,
            ),
        )

        budget = self.config.max_features_per_round
        for fr in pending[:budget]:
            # Token budget gate for feature pipeline
            if not self._token_bucket.can_consume():
                logger.info("token budget exhausted; deferring feature #%s", fr.id)
                break
            try:
                fr_report = pipeline.run(fr, round_num=report.round_num)
                self._token_bucket.consume()
                report.feature_reports.append(fr_report)
                report.features_processed += 1
            except Exception as e:
                logger.exception("FeaturePipeline crashed on #%s: %s", fr.id, e)

        # Feature 开发完成后标记知识图谱需要更新
        if report.features_processed > 0 and self._knowledge_maintainer:
            self._knowledge_maintainer.mark_update_needed()

    # ── stage 3.5: goals auto-decomposition ────────────────────────────────
    def _maybe_decompose_goals(self) -> None:
        """根据配置定时或检测变更自动分解 goals 为 feature requests。"""
        if self.config.dry_run:
            return
        goals_cfg = self.config.goals
        goals_path = Path(goals_cfg.path)
        if not goals_path.is_absolute():
            goals_path = self.deps.repo_root / goals_path
        if not goals_path.exists():
            return

        # 计算当前文件 hash（用于变更检测）
        try:
            current_hash = hashlib.md5(goals_path.read_bytes()).hexdigest()
        except OSError as e:
            logger.debug("read GOALS.md failed: %s", e)
            return

        should_decompose = False
        reason = ""

        # 时间间隔触发（_last_decompose_time 初始为 0 → 首轮必触发）
        now = time.time()
        interval_seconds = max(1, goals_cfg.decompose_interval_hours) * 3600
        if now - self._last_decompose_time >= interval_seconds:
            should_decompose = True
            reason = f"interval ({goals_cfg.decompose_interval_hours}h)"

        # 文件变更触发
        if goals_cfg.decompose_on_change:
            if self._goals_file_hash and current_hash != self._goals_file_hash:
                should_decompose = True
                reason = "GOALS.md changed"

        if not should_decompose:
            # 即便不触发，也要记录初始 hash 以便后续检测变更
            if not self._goals_file_hash:
                self._goals_file_hash = current_hash
            return

        logger.info("Goals auto-decompose triggered: %s", reason)
        try:
            doc = parse_goals(goals_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("parse GOALS.md failed: %s", e)
            # hash 仍更新以避免重复尝试错误内容
            self._goals_file_hash = current_hash
            return

        if not doc.goals:
            self._last_decompose_time = now
            self._goals_file_hash = current_hash
            return

        # 收集已存在的 open features 用于去重
        open_features: list[FeatureRequest] = []
        for status in ("pending", "approved"):
            try:
                open_features.extend(
                    self.deps.storage.list_features(status=status, limit=200)
                )
            except Exception as e:  # pragma: no cover
                logger.debug("list_features(%s) failed: %s", status, e)
        existing_titles_lower = {
            (fr.title or "").strip().lower() for fr in open_features
        }

        # Task #111: 获取真实 KPI 快照和已部署特性，消除分解器感知盲区
        try:
            recent_snapshots = self.deps.storage.get_recent_kpi_snapshots(days=7)
        except Exception as e:  # pragma: no cover
            logger.debug("get_recent_kpi_snapshots failed: %s", e)
            recent_snapshots = []
        try:
            deployed_features = self.deps.storage.get_deployed_features(days=30)
        except Exception as e:  # pragma: no cover
            logger.debug("get_deployed_features failed: %s", e)
            deployed_features = []

        repo_state = RepoState(
            repo_root=str(self.deps.repo_root),
            default_branch=self.config.default_branch,
        )

        # 将最近一次趋势分析产出的健康摘要作为上下文，供后续消费者使用。
        health_context = self._build_health_context()
        if health_context:
            logger.info(
                "Goal decompose health context: grade=%s",
                getattr(self._latest_health_summary, "grade", "?"),
            )

        total_created = 0
        any_failure = False
        for goal in doc.goals:
            try:
                specs = self._goal_decomposer.decompose(
                    goal, repo_state, open_features, recent_snapshots,
                    deployed_features=deployed_features,
                )
            except Exception as e:
                any_failure = True
                logger.warning("decompose goal '%s' failed: %s", goal.name, e)
                continue
            for spec in specs:
                key = (spec.title or "").strip().lower()
                if not key or key in existing_titles_lower:
                    continue
                fid = self._store_feature_request(spec)
                if fid:
                    existing_titles_lower.add(key)
                    total_created += 1

        # 全部失败时不更新时间戳，下轮再试；hash 仍更新
        if not any_failure or total_created > 0:
            self._last_decompose_time = now
        self._goals_file_hash = current_hash
        logger.info(
            "Goals decompose completed: %d feature request(s) created",
            total_created,
        )

    def _build_health_context(self) -> str:
        """将最近一次趋势分析产出的 HealthSummary 渲染为 Markdown 上下文。

        返回空串表示尚未生成趋势分析，调用者可跳过注入。
        """
        h = getattr(self, "_latest_health_summary", None)
        if not h:
            return ""
        try:
            improving = ", ".join(h.improving) if h.improving else "none"
            degrading = ", ".join(h.degrading) if h.degrading else "none"
            risks = ", ".join(h.risks) if h.risks else "none"
            return (
                f"\n## Project Health (Grade: {h.grade})\n"
                f"Improving: {improving}\n"
                f"Degrading: {degrading}\n"
                f"Risks: {risks}\n"
            )
        except Exception as e:  # pragma: no cover
            logger.debug("_build_health_context failed: %s", e)
            return ""

    def _store_feature_request(self, spec: FeatureSpec) -> Optional[int]:
        """将 FeatureSpec 写入 feature_requests 表，返回新 id 或 None。

        Task #111: 目标分解产生的子任务自动进入 approved 状态，
        跳过评估阶段直接进入开发。
        """
        try:
            fr = FeatureRequest(
                title=spec.title,
                description=spec.description,
                type=spec.type,
                parent_goal=spec.parent_goal,
                priority=spec.priority,
                verification_method=spec.verification_method,
                idea_id=getattr(spec, "idea_id", None),
                status="approved",  # auto-approve goal-decomposed features
            )
            return self.deps.storage.create_feature(fr)
        except Exception as e:  # pragma: no cover
            logger.warning("create_feature(decomposed) failed: %s", e)
            return None

    # ── stage 4: KPI health monitor ────────────────────────────────────────
    def _maybe_run_health_monitor(self, *, report: RoundReport) -> None:
        hm = self.deps.health_monitor
        if hm is None or not hm.due():
            return
        try:
            regressions = hm.run()
            if regressions:
                logger.info("health monitor created FRs for %d regressions", len(regressions))
        except Exception as e:  # pragma: no cover
            logger.warning("HealthMonitor.run failed: %s", e)

    # ── stage 5: KPI snapshot capture ──────────────────────────────────
    def _maybe_capture_kpi_snapshot(self) -> None:
        """每轮末尾采集一条 KPI 快照，供趋势 Tab 使用。任何异常都不得中断主循环。"""
        if self.config.dry_run or self._kpi_verifier is None:
            return
        try:
            ctx = ProbeContext(
                repo_root=self.deps.repo_root,
                config=None,  # type: ignore[arg-type]
                storage=self.deps.storage,
                logger=logger.getChild("kpi"),
            )
            snap = self._kpi_verifier.capture(ctx)
            logger.debug("KPI snapshot captured: %d metrics", len(snap.metrics))
        except Exception as e:  # pragma: no cover
            logger.debug("KPI snapshot capture failed: %s", e)

    # ── stage 6: trend analysis (rate-limited) ──────────────────────
    def _maybe_run_trend_analysis(self) -> None:
        """按 ``trend_interval_rounds`` 周期执行趋势分析，生成项目健康摘要。

        健康摘要被存储到 ``self._latest_health_summary``，供
        ``_maybe_decompose_goals`` 构建 GoalDecomposer 上下文。
        失败不影响主循环。
        """
        if self._trend_analyzer is None:
            return
        if not self.config.intelligence.trend_enabled:
            return
        self._trend_round_counter += 1
        if self._trend_round_counter < self.config.intelligence.trend_interval_rounds:
            return
        self._trend_round_counter = 0
        try:
            health = self._trend_analyzer.generate_health_summary()
            self._latest_health_summary = health
            logger.info(
                "Health summary: grade=%s, improving=%s, degrading=%s",
                health.grade,
                ", ".join(health.improving) or "none",
                ", ".join(health.degrading) or "none",
            )
        except Exception as e:
            logger.warning("Trend analysis failed: %s", e)

    # ── helpers ────────────────────────────────────────────────────────────
    def _log_issue_action(
        self,
        run_id: str,
        action_type: str,
        status: str,
        issue: Issue,
        *,
        summary: str = "",
        duration: Optional[float] = None,
        details: Optional[dict] = None,
        pr_url: Optional[str] = None,
        commit_hash: Optional[str] = None,
    ) -> None:
        try:
            self.deps.storage.log_action(
                ActionLog(
                    run_id=run_id,
                    round_num=self._round_num,
                    action_type=action_type,
                    status=status,
                    category=issue.category,
                    level=issue.level.value,
                    title=issue.title,
                    result_summary=summary[:2000],
                    duration_seconds=duration,
                    details={"issue_hash": issue.hash,
                             "source_probe": issue.source_probe,
                             **(details or {})},
                    pr_url=pr_url,
                    commit_hash=commit_hash,
                )
            )
        except Exception as e:  # pragma: no cover
            logger.debug("log_action failed: %s", e)

    @staticmethod
    def _render_issue_pr_body(issue: Issue, *, output: str) -> str:
        return (
            f"## vivify — {issue.category}\n\n"
            f"**Level**: `{issue.level.value}`  "
            f"**Source probe**: `{issue.source_probe}`  "
            f"**Hash**: `{issue.hash}`\n\n"
            f"### Original detection\n"
            f"{issue.description or issue.title}\n\n"
            f"### Agent output (tail)\n"
            f"```\n{(output or '')[-1500:]}\n```\n"
        )

    def _current_code_hash(self) -> str:
        if self.config.package_root is None:
            return ""
        try:
            return compute_code_hash(self.config.package_root)
        except Exception:  # pragma: no cover
            return ""

    def _code_hash_changed(self, current: str) -> bool:
        if not current or not self._initial_code_hash:
            return False
        return current != self._initial_code_hash


__all__ = ["Kernel", "KernelConfig", "KernelDeps", "RoundReport"]
