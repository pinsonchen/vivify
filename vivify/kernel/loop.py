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

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from vivify.agents.history import load_history
from vivify.agents.prompts import builders, parsers
from vivify.fixers.registry import FixerRegistry
from vivify.interfaces.agent import CodingAgent
from vivify.interfaces.fixer import FixContext, FixResult
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
from vivify.models.issue import Issue
from vivify.models.snapshot import ActionLog
from vivify.pr_mode.auto_merge import AutoMerge
from vivify.pr_mode.pr_creator import PrCreator
from vivify.pr_mode.quality_check import run_quality_checks
from vivify.pr_mode.self_grow_guard import classify_worktree
from vivify.pr_mode.worktree import WorktreeManager
from vivify.probes.runner import aggregate_issues, run_probes

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

    # ── public API ─────────────────────────────────────────────────────────
    def run_forever(self, *, max_rounds: Optional[int] = None) -> None:
        """Loop ``run_once`` on ``interval_seconds`` until SIGINT or hash change."""
        while True:
            report = self.run_once()
            if max_rounds is not None and report.round_num >= max_rounds:
                return
            if self._code_hash_changed(report.code_hash):
                logger.warning(
                    "vivify package hash changed (%s → %s); requesting restart",
                    self._initial_code_hash[:12], report.code_hash[:12],
                )
                return
            time.sleep(max(1, self.config.interval_seconds))

    def run_once(self) -> RoundReport:
        self._round_num += 1
        run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        report = RoundReport(run_id=run_id, round_num=self._round_num)
        t0 = time.time()
        try:
            issues = self._detect()
            report.issues_seen = len(issues)
            self._handle_issues(issues, report=report)
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
                self.deps.auto_merge.try_merge(pr, decision=decision, cwd=wt.path)
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

    # ── stage 3: feature pipeline ──────────────────────────────────────────
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
