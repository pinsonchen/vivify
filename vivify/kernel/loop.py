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
from vivify.probes.runner import aggregate_issues, run_probes
from vivify.daemon.lock import InstanceLock
from vivify.daemon.manager import DaemonManager  # 仅用于全局实例注册表
from vivify.config.schema import DaemonConfig, DeployConfig, GoalsConfig
from vivify.deployers import DeployResult, get_deployer

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

        # ── 多实例隔离：获取锁 + 写 PID 文件 ───────────────────────────────
        self._instance_lock: Optional[InstanceLock] = None
        self._pid_file_path: Optional[Path] = None
        self._acquire_instance_lock()

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
        try:
            issues = self._detect()
            report.issues_seen = len(issues)
            self._handle_issues(issues, report=report)
            self._maybe_decompose_goals()
            self._handle_features(report=report)
            self._maybe_run_health_monitor(report=report)
        except Exception as e:
            logger.exception("Kernel round failed: %s", e)
            report.duration_seconds = time.time() - t0
        finally:
            report.duration_seconds = report.duration_seconds or (time.time() - t0)
            report.code_hash = self._current_code_hash()
        return report

    # ── stage 1: detect ────────────────────────────────────────────────────
    def _detect(self) -> list[Issue]:
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
        return aggregate_issues(reports)

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
            if self._try_agent_fix(issue, report=report):
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
        slug = f"{issue.category}-{issue.hash}"
        wt = self.deps.worktrees.create(slug)
        t0 = time.time()
        try:
            history = load_history(self.deps.storage, "fix_issue")
            prompt = builders.build_fix_issue(
                issue, workspace=str(wt.path),
                recent_history=history,
                enable_self_improve=self.config.enable_self_improve_prompt,
            )
            agent_result = self.deps.agent.heal(
                prompt,
                max_turns=30, category="fix_issue",
                workspace=wt.path,
            )
            output = agent_result.output or ""
            quality = run_quality_checks(wt.path, base_ref=wt.base_ref)
            if not quality.passed:
                self._log_issue_action(
                    report.run_id, "heal", "failed", issue,
                    summary=f"quality failed: {quality.summary}",
                    duration=time.time() - t0,
                )
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
            return True
        except Exception as e:
            logger.exception("agent fix failed for %s: %s", issue.hash, e)
            self._log_issue_action(
                report.run_id, "heal", "failed", issue,
                summary=f"exception: {e!r}",
                duration=time.time() - t0,
            )
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
        pipeline = FeaturePipeline(
            agent=self.deps.agent,
            storage=self.deps.storage,
            worktree_mgr=self.deps.worktrees,
            pr_creator=self.deps.pr_creator,
            auto_merge=self.deps.auto_merge,
            run_id=report.run_id,
        )
        for fr in pending[:budget]:
            try:
                fr_report = pipeline.run(fr, round_num=report.round_num)
                report.feature_reports.append(fr_report)
                report.features_processed += 1
            except Exception as e:
                logger.exception("FeaturePipeline crashed on #%s: %s", fr.id, e)

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

        repo_state = RepoState(
            repo_root=str(self.deps.repo_root),
            default_branch=self.config.default_branch,
        )

        total_created = 0
        any_failure = False
        for goal in doc.goals:
            try:
                specs = self._goal_decomposer.decompose(
                    goal, repo_state, open_features, recent_snapshots=[],
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

    def _store_feature_request(self, spec: FeatureSpec) -> Optional[int]:
        """将 FeatureSpec 写入 feature_requests 表，返回新 id 或 None。"""
        try:
            fr = FeatureRequest(
                title=spec.title,
                description=spec.description,
                type=spec.type,
                parent_goal=spec.parent_goal,
                priority=spec.priority,
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
