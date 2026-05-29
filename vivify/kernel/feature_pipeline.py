"""Feature pipeline — evaluate → develop → verify a FeatureRequest.

End-to-end flow for a single FeatureRequest:

1. **evaluate**: read-only LLM pass that scores priority/feasibility and
   produces an ``implementation_approach``. The result is parsed and stored
   on the FeatureRequest.
2. **develop**: spawn a git worktree, run the coding agent inside it, then
   run quality checks. If quality passes, push the branch and open a PR via
   ``pr_mode``. The Jinja templates instruct the agent to commit before the
   process ends.
3. **verify**: optional read-only second pass against the merged commit.
4. **followups**: parse any ``next_steps`` from the develop output and create
   child FeatureRequests linked via ``parent_id``.

Ports the orchestration shape from
``/tmp/channels-monitor/vivify/feature_dev.py`` but stripped of business
specifics: no SSH/rsync, no admin/admin123, no deploy step. Code lands purely
through PR mode.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from vivify.agents.history import load_history
from vivify.agents.prompts import builders, parsers
from vivify.config.schema import AgentCostModel, FeaturePipelineConfig
from vivify.interfaces.agent import CodingAgent
from vivify.interfaces.storage import StorageProvider
from vivify.kernel.feature_states import FeatureStateMachine, InvalidTransitionError
from vivify.kernel.workspace_health import check_workspace_health
from vivify.models.agent_result import AgentResult
from vivify.models.feature import FeatureRequest
from vivify.models.idea import Idea
from vivify.models.snapshot import ActionLog, KnowledgeEntry
from vivify.pr_mode.auto_merge import AutoMerge
from vivify.pr_mode.auto_revert import AutoReverter
from vivify.pr_mode.pr_creator import PrCreator, PullRequest
from vivify.pr_mode.quality_check import QualityCheckResult, run_quality_checks
from vivify.pr_mode.self_grow_guard import classify_worktree
from vivify.pr_mode.worktree import WorktreeManager
from vivify.verifier.metrics_collector import (
    DataDrivenVerifier,
    MetricSnapshot,
    MetricsCollector,
    VerificationVerdict,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────────
# Config + result types
# ────────────────────────────────────────────────────────────────────────────────





@dataclass
class FeatureRunReport:
    feature_id: int
    status: str = "pending"
    pr: Optional[PullRequest] = None
    quality: Optional[QualityCheckResult] = None
    followups_created: int = 0
    error: Optional[str] = None
    durations: dict = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────────────
# Pipeline
# ────────────────────────────────────────────────────────────────────────────────


class FeaturePipeline:
    """Drive a single FeatureRequest through evaluate → develop → verify."""

    def __init__(
        self,
        *,
        agent: CodingAgent,
        storage: StorageProvider,
        worktree_mgr: WorktreeManager,
        pr_creator: PrCreator,
        auto_merge: Optional[AutoMerge] = None,
        config: FeaturePipelineConfig | None = None,
        run_id: str = "manual",
    ):
        self.agent = agent
        self.storage = storage
        self.worktrees = worktree_mgr
        self.pr_creator = pr_creator
        self.auto_merge = auto_merge
        self.config = config or FeaturePipelineConfig()
        self.run_id = run_id

    # ── public entry point ─────────────────────────────────────────────────

    def _get_agent_params(self, feature: FeatureRequest) -> tuple[int, int]:
        """Return (max_turns, timeout) based on feature priority via cost model."""
        cost = self.config.cost_model
        priority = (getattr(feature, "priority", None) or "P2").upper()
        params = {
            "P0": (cost.p0_max_turns, cost.p0_timeout),
            "P1": (cost.p1_max_turns, cost.p1_timeout),
            "P2": (cost.p2_max_turns, cost.p2_timeout),
            "P3": (cost.p3_max_turns, cost.p3_timeout),
        }
        return params.get(priority, (cost.p2_max_turns, cost.p2_timeout))

    def run(self, feature: FeatureRequest, *, round_num: int = 0) -> FeatureRunReport:
        report = FeatureRunReport(feature_id=feature.id, status=feature.status)
        try:
            self.evaluate(feature, report=report, round_num=round_num)
            if feature.status == "rejected":
                return report
            self.develop(feature, report=report, round_num=round_num)
            if feature.status in ("verified", "deployed"):
                self.verify(feature, report=report, round_num=round_num)
        except Exception as e:
            report.error = f"pipeline error: {e!r}"
            logger.exception("Feature pipeline failed for #%s", feature.id)
            self._update(feature, status="rejected", summary=str(e))
        return report

    # ── stage 1: evaluate ──────────────────────────────────────────────────
    def evaluate(
        self,
        feature: FeatureRequest,
        *,
        report: FeatureRunReport,
        round_num: int,
    ) -> None:
        start = time.time()
        prompt = builders.build_feature_evaluate(feature)
        result = self.agent.heal(
            prompt,
            max_turns=self.config.max_turns_evaluate,
            category="evaluate_feature",
            workspace=self.worktrees.repo_root,
            timeout_seconds=self.config.timeout_evaluate_seconds,
        )
        parsed = parsers.parse_evaluation_result(result.output or "")
        feature.priority = parsed.get("priority") or feature.priority
        feature.feasibility = parsed.get("feasibility") or ""
        feature.summary = parsed.get("summary") or ""

        # ── ROI 门槛检查 ──────────────────────────────────────────────────
        roi_score = parsed.get("roi_score", 100)  # 默认 100 保证向后兼容
        if isinstance(roi_score, (int, float)) and roi_score < 30:
            logger.info("Feature #%d rejected: ROI too low (%d/100)", feature.id, roi_score)
            feature.feasibility = f"ROI too low ({roi_score}/100) for automated execution"
            parsed["feasible"] = False

        # ── 部署可行性检查 ────────────────────────────────────────────────
        if parsed.get("needs_admin_review", False):
            logger.info("Feature #%d rejected: needs admin review", feature.id)
            feature.feasibility = parsed.get("feasibility") or "Requires manual admin review"
            parsed["feasible"] = False

        if parsed.get("blocked_by_parent", False):
            logger.info("Feature #%d rejected: blocked by undeployed parent", feature.id)
            feature.feasibility = parsed.get("feasibility") or "Blocked by undeployed parent feature"
            parsed["feasible"] = False

        if not parsed.get("feasible", True):
            self._update(feature, status="rejected",
                         feasibility=feature.feasibility, summary=feature.summary)
            self._log_action(
                round_num=round_num,
                action_type="feature_evaluate",
                status="success",
                feature=feature,
                summary=feature.summary,
                details={"feasible": False, "roi_score": roi_score,
                         "needs_admin_review": parsed.get("needs_admin_review", False),
                         "blocked_by_parent": parsed.get("blocked_by_parent", False),
                         "approach": parsed.get("implementation_approach", "")},
                duration=time.time() - start,
            )
            report.status = "rejected"
            return

        self._update(
            feature, status="approved",
            priority=feature.priority, feasibility=feature.feasibility, summary=feature.summary,
        )
        # Stash the approach for the develop stage to inject.
        object.__setattr__(feature, "_approach", parsed.get("implementation_approach", ""))
        # Update verification_method if the evaluator refined it.
        refined_vm = (parsed.get("refined_verification_method") or "").strip()
        if refined_vm:
            feature.verification_method = refined_vm
            self._update(feature, verification_method=refined_vm)
        report.durations["evaluate"] = time.time() - start
        self._log_action(
            round_num=round_num, action_type="feature_evaluate",
            status="success", feature=feature, summary=feature.summary,
            details={"approach": parsed.get("implementation_approach", "")},
            duration=report.durations["evaluate"],
        )

    # ── stage 2: develop ───────────────────────────────────────────────────
    def develop(
        self,
        feature: FeatureRequest,
        *,
        report: FeatureRunReport,
        round_num: int,
    ) -> None:
        start = time.time()

        # Pre-flight workspace health check
        health = check_workspace_health(self.worktrees.repo_root)
        if not health.passed:
            logger.warning(
                "Pre-flight check failed for feature #%s: %s",
                feature.id, health.summary,
            )
            self._log_action(
                round_num=round_num, action_type="feature_develop",
                status="skipped", feature=feature,
                summary=f"pre-flight failed: {health.summary}",
                duration=time.time() - start,
            )
            report.status = feature.status  # keep current status unchanged
            return

        slug = feature.title or f"feature-{feature.id}"
        wt = self.worktrees.create(slug)
        result: Optional[AgentResult] = None
        # Task #73: resolve agent budget from cost model based on priority
        max_turns, timeout = self._get_agent_params(feature)
        # Task #119: collect baseline metrics before development
        if self.config.data_driven_verification:
            self._collect_baseline_metrics(feature)
        try:
            self._update(feature, status="developing")
            history = load_history(self.storage, "feature_develop")
            approach = getattr(feature, "_approach", "")
            prompt = builders.build_feature_develop(
                feature, workspace=str(wt.path),
                recent_history=history,
                implementation_approach=approach,
            )
            # Task #129: capture HEAD before agent runs so we can tell
            # whether *this round* produced new commits, independent of any
            # historical commits left over in a reused worktree.
            before_sha = self._get_head_sha(wt.path)
            result = self.agent.heal(
                prompt,
                max_turns=max_turns,
                category="develop_feature",
                workspace=wt.path,
                timeout_seconds=timeout,
            )
            output = result.output or ""
            after_sha = self._get_head_sha(wt.path)
            has_new_commits = bool(after_sha) and (before_sha != after_sha)

            # Quality gate inside the worktree.
            quality = run_quality_checks(
                wt.path,
                base_ref=wt.base_ref,
                run_pytest=self.config.quality_run_pytest,
                test_command=self.config.quality_test_command,
            )
            report.quality = quality
            if not quality.passed:
                self._update(
                    feature, status="deployed_with_issues",
                    development_result=(quality.summary or "")[:2000],
                )
                self._log_action(
                    round_num=round_num, action_type="feature_develop",
                    status="failed", feature=feature,
                    summary=f"quality check failed: {quality.summary}"[:2000],
                    details={"quality": quality.summary},
                    duration=time.time() - start,
                )
                report.status = "deployed_with_issues"
                return

            # ── Task #129: per-round HEAD comparison ──
            # Distinguish "this round produced no new commits" from
            # "worktree has no diff vs base" (the latter is also checked
            # below as a final sanity guard before pushing).
            if not has_new_commits:
                new_retry = int(getattr(feature, "retry_count", 0) or 0) + 1
                max_empty = self.config.max_retries
                if new_retry >= max_empty:
                    logger.warning(
                        "Feature #%s: agent produced no new commits for "
                        "%d consecutive rounds; escalating to "
                        "deployed_with_issues for human review",
                        feature.id, new_retry,
                    )
                    self._update(
                        feature,
                        status="deployed_with_issues",
                        retry_count=new_retry,
                        development_result=(
                            "Agent produced no new commits this round; "
                            f"escalated after {new_retry} attempts "
                            f"(max_retries={max_empty})"
                        )[:2000],
                    )
                    report.status = "deployed_with_issues"
                    return
                logger.warning(
                    "Feature #%s: agent produced no new commits this round "
                    "(before=%s, after=%s); rolling back to approved for "
                    "retry %d/%d",
                    feature.id, before_sha[:8], after_sha[:8],
                    new_retry, max_empty,
                )
                self._update(
                    feature,
                    status="approved",
                    retry_count=new_retry,
                    development_result=(
                        "Agent produced no new commits this round; "
                        f"will retry (attempt {new_retry}/{max_empty})"
                    ),
                )
                report.status = "approved"
                return

            # ── Final sanity guard: ensure worktree as a whole has changes vs base.
            # This catches the rare case where new commits were produced
            # but they net out to zero diff against base_ref.
            if not self._has_actual_changes(wt.path, wt.base_ref):
                logger.warning(
                    "Feature #%s: worktree has new commits but no diff "
                    "against %s; rolling back to approved",
                    feature.id, wt.base_ref,
                )
                new_retry = int(getattr(feature, "retry_count", 0) or 0) + 1
                self._update(
                    feature,
                    status="approved",
                    retry_count=new_retry,
                    development_result=(
                        "Worktree has no commits relative to base; "
                        f"will retry (attempt {new_retry})"
                    ),
                )
                report.status = "approved"
                return

            commit_info = parsers.parse_commit_info(output, repo_url=self.config.repo_url)
            decision = classify_worktree(wt.path, base_ref=wt.base_ref)
            try:
                pr = self.pr_creator.push_and_open(
                    wt,
                    title=feature.title[:200],
                    body=self._render_pr_body(feature, output=output, quality=quality),
                    decision=decision,
                )
            except Exception as pr_err:
                # PR creation can fail for transient reasons (missing
                # GH_TOKEN in the subprocess env, gh quota, label issues,
                # network blips, ...). Mark the feature as
                # ``deployed_with_issues`` so the next run can retry it,
                # rather than letting the exception bubble up to the
                # outer ``except`` which would reject the feature outright.
                logger.error(
                    "PR creation failed for feature #%s: %s", feature.id, pr_err,
                )
                self._update(
                    feature, status="deployed_with_issues",
                    development_result=(
                        f"PR creation failed: {pr_err}"
                    )[:2000],
                )
                # NB: the outer ``finally`` already emits an ActionLog for
                # ``feature_develop`` based on ``report.status``; don't
                # duplicate it here.
                report.status = "deployed_with_issues"
                report.error = f"pr_create: {pr_err}"
                return
            report.pr = pr
            self._update(
                feature, status="deployed",
                pr_url=pr.url,
                commit_hash=commit_info.get("commit_hash"),
                development_result=(output[-2000:] if output else ""),
            )

            if self.auto_merge is not None:
                merge_outcome = self.auto_merge.try_merge(pr, decision=decision, cwd=wt.path)
                if merge_outcome.merged:
                    logger.info("Feature PR #%s merged successfully", feature.id)
                elif merge_outcome.requested and not merge_outcome.merged:
                    logger.info(
                        "Feature PR #%s auto-merge requested but not yet merged (timeout)",
                        feature.id,
                    )

            # Followups
            followups = self._create_followups(feature, output)
            report.followups_created = followups
            report.status = "deployed"
        finally:
            try:
                self.worktrees.remove(wt)
            except Exception as e:  # pragma: no cover
                logger.warning("worktree cleanup failed: %s", e)
            elapsed = time.time() - start
            report.durations["develop"] = elapsed
            # Task #73: append cost info to development_result
            cost_info = (
                f"\n[cost] priority={getattr(feature, 'priority', '?')}, "
                f"max_turns={max_turns}, timeout={timeout}, elapsed={elapsed:.0f}s"
            )
            prev_result = getattr(feature, "development_result", None) or ""
            self._update(feature, development_result=(prev_result + cost_info)[-2000:])
            self._log_action(
                round_num=round_num, action_type="feature_develop",
                status="success" if report.status == "deployed" else "failed",
                feature=feature,
                summary=(result.output[-1000:] if result and result.output else "")[:2000],
                details={"followups": report.followups_created,
                         "pr_url": report.pr.url if report.pr else None,
                         "cost_max_turns": max_turns,
                         "cost_timeout": timeout,
                         "cost_elapsed": round(elapsed)},
                duration=elapsed,
                pr_url=report.pr.url if report.pr else None,
                commit_hash=feature.commit_hash,
            )

    # ── stage 3: verify ────────────────────────────────────────────────────
    def verify(
        self,
        feature: FeatureRequest,
        *,
        report: FeatureRunReport,
        round_num: int,
    ) -> None:
        start = time.time()
        # Mark verifying so the recovery sweep can detect a stuck verify run.
        self._update(feature, status="verifying")

        # ── Task #119: data-driven verification (pre-LLM) ────────────────────
        data_driven_verdict: Optional[VerificationVerdict] = None
        if self.config.data_driven_verification:
            data_driven_verdict = self._run_data_driven_verification(feature)
            if data_driven_verdict and not data_driven_verdict.requires_llm_review:
                # High-confidence automated verdict — skip LLM verification
                verified = data_driven_verdict.passed
                new_status = "verified" if verified else "deployed_with_issues"
                verification_data = {
                    "verified": verified,
                    "data_driven": True,
                    "confidence": data_driven_verdict.confidence,
                    "reason": data_driven_verdict.reason,
                    "metrics_delta": (
                        data_driven_verdict.metrics_delta.summary
                        if data_driven_verdict.metrics_delta else ""
                    ),
                }
                try:
                    dd_json = json.dumps(
                        verification_data, ensure_ascii=False
                    )[:4000]
                except (TypeError, ValueError):
                    dd_json = None

                dd_update: dict = {
                    "status": new_status,
                    "summary": data_driven_verdict.reason[:500],
                }
                if dd_json:
                    dd_update["verification_result"] = dd_json
                self._update(feature, **dd_update)

                report.durations["verify"] = time.time() - start
                report.status = new_status
                self._log_action(
                    round_num=round_num, action_type="feature_verify",
                    status="success" if verified else "failed",
                    feature=feature,
                    summary=f"[data-driven] {data_driven_verdict.reason}"[:2000],
                    details={
                        "data_driven": True,
                        "confidence": data_driven_verdict.confidence,
                        "verdict": "verified" if verified else "failed",
                    },
                    duration=report.durations["verify"],
                )
                return

        # ── LLM-based verification (fallback or when confidence is low) ──────
        prompt = builders.build_feature_verify(feature)
        result = self.agent.heal(
            prompt,
            max_turns=self.config.max_turns_verify,
            category="verify_feature",
            workspace=self.worktrees.repo_root,
            timeout_seconds=self.config.timeout_verify_seconds,
        )
        parsed = parsers.parse_verification_result(result.output or "")
        verified = bool(parsed.get("verified")) and not parsed.get("parse_failed")
        new_status = "verified" if verified else "deployed_with_issues"

        # ── result-oriented verification: persist business-impact metrics ──
        verification_data = {
            "verified": verified,
            "metrics_before": parsed.get("metrics_before", {}) or {},
            "metrics_after": parsed.get("metrics_after", {}) or {},
            "improvement_summary": parsed.get("improvement_summary", "") or "",
            "regression_detected": bool(parsed.get("regression_detected", False)),
            "verdict": parsed.get("verdict")
                or ("verified" if verified else "failed"),
            "issues": parsed.get("issues", []) or [],
        }
        try:
            verification_result_json = json.dumps(
                verification_data, ensure_ascii=False
            )[:4000]
        except (TypeError, ValueError):
            verification_result_json = None

        update_fields: dict = {
            "status": new_status,
            "summary": parsed.get("summary", feature.summary),
        }
        if verification_result_json:
            update_fields["verification_result"] = verification_result_json
        self._update(feature, **update_fields)

        # Persist a knowledge entry — what worked / what didn't.
        try:
            self.storage.add_knowledge(
                KnowledgeEntry(
                    category="bug_fix" if feature.type == "bug" else "feature",
                    pattern=feature.title,
                    solution_summary=(parsed.get("summary") or feature.summary)[:500],
                    success=verified,
                    feature_id=feature.id,
                )
            )
        except Exception as e:  # pragma: no cover
            logger.debug("add_knowledge failed: %s", e)

        # ── auto-derive a fix request when verification fails ─────────────
        if not verified:
            issues = parsed.get("issues") or []
            failure_reason = (
                "; ".join(str(i) for i in issues)
                if issues
                else (parsed.get("summary") or "verification failed")
            )
            try:
                self._create_derived_feature(
                    parent=feature,
                    title=f"Fix verification failure: {failure_reason[:60]}",
                    description=(
                        f"Verification of #{feature.id} failed: "
                        f"{failure_reason}. Auto-generated fix request."
                    ),
                    type="bug",
                )
            except Exception as derive_err:  # pragma: no cover
                logger.warning(
                    "failed to create derived feature for #%s: %s",
                    feature.id, derive_err,
                )

        report.durations["verify"] = time.time() - start
        report.status = new_status
        self._log_action(
            round_num=round_num, action_type="feature_verify",
            status="success" if verified else "failed",
            feature=feature, summary=parsed.get("summary", "")[:2000],
            details={
                "issues": parsed.get("issues", []),
                "verdict": verification_data["verdict"],
                "regression_detected": verification_data["regression_detected"],
                "improvement_summary": verification_data["improvement_summary"],
            },
            duration=report.durations["verify"],
        )

    # ── parallel (batch) development ────────────────────────────────────────

    def develop_batch(
        self,
        features: list[FeatureRequest],
        *,
        round_num: int = 0,
    ) -> list[FeatureRunReport]:
        """Develop multiple features in parallel via remote sessions.

        When the agent has a :attr:`remote_mgr` and ``use_remote`` is enabled,
        remote sessions are launched concurrently up to
        ``max_concurrent_remote``.  Otherwise falls back to serial execution.
        """
        remote_mgr = getattr(self.agent, "remote_mgr", None)
        agent_cfg = getattr(self.agent, "cfg", None)

        # Fallback: no remote manager → serial
        if not remote_mgr or not agent_cfg or not getattr(agent_cfg, "use_remote", False):
            reports: list[FeatureRunReport] = []
            for f in features:
                reports.append(self.run(f, round_num=round_num))
            return reports

        max_concurrent: int = getattr(agent_cfg, "max_concurrent_remote", 3)
        poll_interval: int = getattr(agent_cfg, "remote_poll_interval", 15)
        timeout: int = getattr(agent_cfg, "remote_timeout", 900)

        # sessions maps feature_id → (RemoteSession, feature, Worktree)
        sessions: dict = {}
        reports = []

        # Launch remote sessions (bounded by max_concurrent)
        for feature in features[:max_concurrent]:
            report = FeatureRunReport(feature_id=feature.id, status="developing")
            reports.append(report)
            try:
                prompt = self._build_develop_prompt(feature)
                wt = self._prepare_worktree(feature)
                # Task #129: snapshot HEAD before remote session starts so
                # finalize can detect whether new commits were produced.
                try:
                    object.__setattr__(
                        wt, "_before_sha", self._get_head_sha(wt.path),
                    )
                except Exception:  # pragma: no cover
                    pass
                session = remote_mgr.create_session(
                    task=prompt,
                    workspace=wt.path,
                    max_turns=self.config.max_turns_develop,
                )
                sessions[feature.id] = (session, feature, wt, report)
                self._update(feature, status="developing")
            except Exception as e:
                logger.error(
                    "Failed to start remote session for %s: %s", feature.title, e
                )
                self._handle_remote_failure(feature, report)

        # Poll all active sessions
        start = time.time()
        while sessions and (time.time() - start) < timeout:
            for fid in list(sessions.keys()):
                session, feature, wt, report = sessions[fid]
                status = remote_mgr.check_status(session.session_id)
                if status == "completed":
                    del sessions[fid]
                    self._finalize_remote_feature(
                        feature, wt, report, remote_mgr, round_num=round_num
                    )
                elif status == "failed":
                    del sessions[fid]
                    self._handle_remote_failure(feature, report)
                    try:
                        self.worktrees.remove(wt)
                    except Exception as exc:
                        logger.warning("worktree cleanup failed: %s", exc)
            if sessions:
                time.sleep(poll_interval)

        # Timeout remaining sessions
        for fid, (session, feature, wt, report) in sessions.items():
            logger.warning(
                "Remote session timed out for feature: %s", feature.title
            )
            self._handle_remote_failure(feature, report)
            try:
                self.worktrees.remove(wt)
            except Exception as exc:
                logger.warning("worktree cleanup failed: %s", exc)

        return reports

    # ── batch helpers ──────────────────────────────────────────────────────

    def _build_develop_prompt(self, feature: FeatureRequest) -> str:
        """Construct the develop prompt for *feature* (reuses prompt builder)."""
        history = load_history(self.storage, "feature_develop")
        approach = getattr(feature, "_approach", "")
        slug = feature.title or f"feature-{feature.id}"
        # workspace placeholder — will be replaced by actual worktree path later
        # but builder needs a string.
        return builders.build_feature_develop(
            feature,
            workspace=slug,
            recent_history=history,
            implementation_approach=approach,
        )

    def _prepare_worktree(self, feature: FeatureRequest):
        """Create a git worktree for *feature* development."""
        slug = feature.title or f"feature-{feature.id}"
        return self.worktrees.create(slug)

    def _finalize_remote_feature(
        self,
        feature: FeatureRequest,
        wt,
        report: FeatureRunReport,
        remote_mgr,
        *,
        round_num: int = 0,
    ) -> None:
        """Post-process a successfully completed remote feature session."""
        start = time.time()
        try:
            output = remote_mgr.get_result(
                getattr(wt, "_session_id", "") or "", wt.path
            ) if hasattr(wt, "path") else ""
            # Attempt to get result from git log in worktree
            output = remote_mgr.get_result("", wt.path)

            # Quality gate
            quality = run_quality_checks(
                wt.path,
                base_ref=wt.base_ref,
                run_pytest=self.config.quality_run_pytest,
                test_command=self.config.quality_test_command,
            )
            report.quality = quality
            if not quality.passed:
                self._update(
                    feature, status="deployed_with_issues",
                    development_result=(quality.summary or "")[:2000],
                )
                report.status = "deployed_with_issues"
                return

            # Task #129: HEAD-based per-round change detection
            before_sha = getattr(wt, "_before_sha", "") or ""
            after_sha = self._get_head_sha(wt.path)
            has_new_commits = bool(after_sha) and (before_sha != after_sha)
            if not has_new_commits:
                new_retry = int(getattr(feature, "retry_count", 0) or 0) + 1
                max_empty = self.config.max_retries
                if new_retry >= max_empty:
                    logger.warning(
                        "Feature #%s (batch): agent produced no new commits "
                        "for %d consecutive rounds; escalating",
                        feature.id, new_retry,
                    )
                    self._update(
                        feature,
                        status="deployed_with_issues",
                        retry_count=new_retry,
                        development_result=(
                            "Agent produced no new commits this round; "
                            f"escalated after {new_retry} attempts"
                        )[:2000],
                    )
                    report.status = "deployed_with_issues"
                    return
                logger.warning(
                    "Feature #%s (batch): no new commits this round, "
                    "rolling back to approved (retry %d/%d)",
                    feature.id, new_retry, max_empty,
                )
                self._update(
                    feature,
                    status="approved",
                    retry_count=new_retry,
                    development_result=(
                        "Agent produced no new commits this round; "
                        f"will retry (attempt {new_retry}/{max_empty})"
                    ),
                )
                report.status = "approved"
                return

            # Final sanity guard: worktree must have changes vs base before push
            if not self._has_actual_changes(wt.path, wt.base_ref):
                logger.warning(
                    "Feature #%s (batch): worktree has new commits but no "
                    "diff vs %s; rolling back",
                    feature.id, wt.base_ref,
                )
                new_retry = int(getattr(feature, "retry_count", 0) or 0) + 1
                self._update(
                    feature,
                    status="approved",
                    retry_count=new_retry,
                    development_result=(
                        "Worktree has no commits relative to base; "
                        f"will retry (attempt {new_retry})"
                    ),
                )
                report.status = "approved"
                return

            # PR creation
            commit_info = parsers.parse_commit_info(output, repo_url=self.config.repo_url)
            decision = classify_worktree(wt.path, base_ref=wt.base_ref)
            try:
                pr = self.pr_creator.push_and_open(
                    wt,
                    title=feature.title[:200],
                    body=self._render_pr_body(feature, output=output, quality=quality),
                    decision=decision,
                )
            except Exception as pr_err:
                logger.error(
                    "PR creation failed for feature #%s: %s", feature.id, pr_err
                )
                self._update(
                    feature, status="deployed_with_issues",
                    development_result=f"PR creation failed: {pr_err}"[:2000],
                )
                report.status = "deployed_with_issues"
                report.error = f"pr_create: {pr_err}"
                return

            report.pr = pr
            self._update(
                feature, status="deployed",
                pr_url=pr.url,
                commit_hash=commit_info.get("commit_hash"),
                development_result=(output[-2000:] if output else ""),
            )

            # Auto-merge
            if self.auto_merge is not None:
                merge_outcome = self.auto_merge.try_merge(
                    pr, decision=decision, cwd=wt.path
                )
                if merge_outcome.merged:
                    logger.info("Feature PR #%s merged (batch)", feature.id)

            # Followups
            followups = self._create_followups(feature, output)
            report.followups_created = followups
            report.status = "deployed"
        except Exception as e:
            logger.exception(
                "Finalization failed for feature #%s: %s", feature.id, e
            )
            self._handle_remote_failure(feature, report)
        finally:
            report.durations["develop"] = time.time() - start
            self._log_action(
                round_num=round_num,
                action_type="feature_develop",
                status="success" if report.status == "deployed" else "failed",
                feature=feature,
                summary="(batch remote)",
                details={"followups": report.followups_created,
                         "pr_url": report.pr.url if report.pr else None},
                duration=report.durations["develop"],
                pr_url=report.pr.url if report.pr else None,
                commit_hash=feature.commit_hash,
            )
            try:
                self.worktrees.remove(wt)
            except Exception as exc:
                logger.warning("worktree cleanup failed: %s", exc)

    def _handle_remote_failure(
        self, feature: FeatureRequest, report: FeatureRunReport
    ) -> None:
        """Mark a feature as failed after remote session error/timeout."""
        self._update(feature, status="deployed_with_issues",
                     development_result="Remote session failed or timed out")
        report.status = "deployed_with_issues"
        report.error = "remote_session_failed"

    # ── derived requirements & batch development ──────────────────────────

    def _create_derived_feature(
        self,
        parent: FeatureRequest,
        title: str,
        description: str,
        type: str = "bug",
    ) -> Optional[int]:
        """Spawn a child FeatureRequest from a verification/deploy failure.

        The derived feature inherits ``parent_goal``/``priority`` from the
        parent, links back via ``parent_id``, and is created in
        ``approved`` status so it skips re-evaluation and immediately enters
        the develop stage on the next round.
        """
        try:
            depth = self._get_chain_depth(parent.id)
        except Exception:
            depth = 0
        if depth >= 2:
            logger.info(
                "Derived chain depth %d >= 2; skipping derivation from #%s",
                depth, parent.id,
            )
            return None

        derived = FeatureRequest(
            title=f"[derived #{parent.id}] {title}"[:200],
            description=description[:4000],
            type=type if type in ("feature", "bug", "optimization") else "bug",
            priority=getattr(parent, "priority", None) or "P1",
            parent_id=parent.id,
            parent_goal=getattr(parent, "parent_goal", None),
            status="approved",  # skip evaluation, go straight to develop
            verification_method=(
                f"Verify that the issue from feature #{parent.id} is "
                f"resolved: {title}"
            ),
        )
        try:
            fid = self.storage.create_feature(derived)
        except Exception as e:  # pragma: no cover
            logger.warning("create_feature(derived) failed: %s", e)
            return None
        logger.info(
            "Created derived feature #%s from #%s: %s",
            fid, parent.id, title,
        )
        return fid

    def _maybe_batch_develop(
        self, features: list[FeatureRequest]
    ) -> list[FeatureRequest]:
        """Group 3+ low-priority (P2/P3) features into a single batch.

        Returns the selected batch (up to 5 features) tagged with a shared
        ``batch_commit_hash``.  Returns ``[]`` when the pool is too small.
        """
        low_priority = [
            f for f in features
            if getattr(f, "priority", "") in ("P2", "P3")
        ]
        if len(low_priority) < 3:
            return []

        batch = low_priority[:5]
        batch_titles = "|".join(f.title or "" for f in batch)
        batch_hash = hashlib.md5(
            batch_titles.encode("utf-8")
        ).hexdigest()[:12]

        for f in batch:
            f.batch_commit_hash = batch_hash
            try:
                self.storage.update_feature(
                    f.id, batch_commit_hash=batch_hash,
                )
            except Exception as e:  # pragma: no cover
                logger.debug(
                    "update_feature(batch tag) failed for #%s: %s",
                    f.id, e,
                )
        logger.info(
            "Batch %s: grouping %d low-priority features for combined development",
            batch_hash, len(batch),
        )
        return batch

    def _build_batch_prompt(self, features: list[FeatureRequest]) -> str:
        """Render a single develop prompt that bundles multiple features."""
        lines: list[str] = [
            "Implement the following features in a single commit:\n",
        ]
        for i, f in enumerate(features, 1):
            lines.append(f"## Feature {i}: {f.title}")
            lines.append(f"Description: {f.description}")
            if f.verification_method:
                lines.append(f"Verification: {f.verification_method}")
            lines.append("")
        lines.append(
            "Implement all features together. Create a single "
            "well-organized commit covering every item above."
        )
        return "\n".join(lines)

    # ── derived requirements & batch development ──────────────────────────

    def _create_derived_feature(
        self,
        parent: FeatureRequest,
        title: str,
        description: str,
        type: str = "bug",
    ) -> Optional[int]:
        """Spawn a child FeatureRequest from a verification/deploy failure.

        The derived feature inherits ``parent_goal``/``priority`` from the
        parent, links back via ``parent_id``, and is created in
        ``approved`` status so it skips re-evaluation and immediately enters
        the develop stage on the next round.
        """
        try:
            depth = self._get_chain_depth(parent.id)
        except Exception:
            depth = 0
        if depth >= 2:
            logger.info(
                "Derived chain depth %d >= 2; skipping derivation from #%s",
                depth, parent.id,
            )
            return None

        derived_type = type if type in ("feature", "bug", "optimization") else "bug"
        derived = FeatureRequest(
            title=f"[derived #{parent.id}] {title}"[:200],
            description=description[:4000],
            type=derived_type,
            priority=getattr(parent, "priority", None) or "P1",
            parent_id=parent.id,
            parent_goal=getattr(parent, "parent_goal", None),
            status="approved",  # skip evaluation, go straight to develop
            verification_method=(
                f"Verify that the issue from feature #{parent.id} is "
                f"resolved: {title}"
            ),
        )
        try:
            fid = self.storage.create_feature(derived)
        except Exception as e:  # pragma: no cover
            logger.warning("create_feature(derived) failed: %s", e)
            return None
        logger.info(
            "Created derived feature #%s from #%s: %s",
            fid, parent.id, title,
        )
        return fid

    def _maybe_batch_develop(
        self, features: list[FeatureRequest]
    ) -> list[FeatureRequest]:
        """Group 3+ low-priority (P2/P3) features into a single batch.

        Returns the selected batch (up to 5 features) tagged with a shared
        ``batch_commit_hash``.  Returns ``[]`` when the pool is too small.
        """
        low_priority = [
            f for f in features
            if getattr(f, "priority", "") in ("P2", "P3")
        ]
        if len(low_priority) < 3:
            return []

        batch = low_priority[:5]
        batch_titles = "|".join(f.title or "" for f in batch)
        batch_hash = hashlib.md5(
            batch_titles.encode("utf-8")
        ).hexdigest()[:12]

        for f in batch:
            f.batch_commit_hash = batch_hash
            try:
                self.storage.update_feature(
                    f.id, batch_commit_hash=batch_hash,
                )
            except Exception as e:  # pragma: no cover
                logger.debug(
                    "update_feature(batch tag) failed for #%s: %s",
                    f.id, e,
                )
        logger.info(
            "Batch %s: grouping %d low-priority features for combined development",
            batch_hash, len(batch),
        )
        return batch

    def _build_batch_prompt(self, features: list[FeatureRequest]) -> str:
        """Render a single develop prompt that bundles multiple features."""
        lines: list[str] = [
            "Implement the following features in a single commit:\n",
        ]
        for i, f in enumerate(features, 1):
            lines.append(f"## Feature {i}: {f.title}")
            lines.append(f"Description: {f.description}")
            if f.verification_method:
                lines.append(f"Verification: {f.verification_method}")
            lines.append("")
        lines.append(
            "Implement all features together. Create a single "
            "well-organized commit covering every item above."
        )
        return "\n".join(lines)

    # ── helpers ────────────────────────────────────────────────────────────
    def _get_chain_depth(self, feature_id: int) -> int:
        """Recursively measure followup chain depth (trace parent_id upward)."""
        depth = 0
        current_id = feature_id
        seen: set[int] = set()  # guard against circular references
        while current_id and current_id not in seen:
            seen.add(current_id)
            parent = self.storage.get_feature(current_id)
            if parent and getattr(parent, 'parent_id', None):
                depth += 1
                current_id = parent.parent_id
            else:
                break
        return depth

    def _create_followups(self, parent: FeatureRequest, output: str) -> int:
        # Enforce followup chain depth limit (max 2 levels)
        depth = self._get_chain_depth(parent.id)
        if depth >= 2:
            logger.info(
                "Followup chain depth %d >= 2, skipping followup for feature #%d",
                depth, parent.id,
            )
            return 0

        steps = parsers.parse_next_steps(output)[: self.config.max_followups]
        created = 0
        for step in steps:
            try:
                self.storage.create_feature(
                    FeatureRequest(
                        title=f"[followup #{parent.id}] {step['title']}",
                        description=step["description"],
                        type="feature",
                        parent_id=parent.id,
                        parent_goal=parent.parent_goal,
                    )
                )
                created += 1
            except Exception as e:  # pragma: no cover
                logger.warning("create_feature(followup) failed: %s", e)
        return created

    def _update(self, feature: FeatureRequest, **fields) -> None:
        # ── Task #65: auto-record lifecycle timestamps on status changes ──
        new_status = fields.get("status")
        if new_status:
            now_iso = datetime.now(timezone.utc).isoformat()
            if new_status == "evaluating":
                fields.setdefault("evaluated_at", now_iso)
            elif new_status == "developing":
                fields.setdefault("started_at", now_iso)
            elif new_status == "verifying":
                # mark when verification phase starts; preserve started_at
                pass
            elif new_status == "verified":
                fields.setdefault("verified_at", now_iso)
                fields.setdefault("completed_at", now_iso)
            elif new_status == "rejected":
                fields.setdefault("completed_at", now_iso)
            elif new_status == "deployed_with_issues":
                fields.setdefault("verified_at", now_iso)

        for k, v in fields.items():
            if hasattr(feature, k):
                setattr(feature, k, v)
        if feature.id:
            try:
                self.storage.update_feature(feature.id, **fields)
            except Exception as e:  # pragma: no cover
                logger.warning("update_feature(%s) failed: %s", feature.id, e)

        # ── Task #115: auto-complete Idea when all child FRs are done ──
        if new_status in ("verified", "deployed") and getattr(feature, "idea_id", None):
            self._maybe_complete_idea(feature.idea_id)

    def _update_feature_status(
        self, feature: FeatureRequest, new_status: str, **extra_fields,
    ) -> None:
        """统一状态转移入口 — 通过状态机校验后再更新。

        Validates the transition via :class:`FeatureStateMachine` before
        delegating to :meth:`_update`.  Invalid transitions are logged as
        warnings and silently skipped (production-safe).
        """
        old_status = feature.status

        # 状态机校验
        try:
            FeatureStateMachine.validate_transition(old_status, new_status)
        except InvalidTransitionError as e:
            logger.warning(
                "Feature #%s: %s — skipping transition", feature.id, e,
            )
            return  # 静默跳过非法转移（生产环境不中断）

        self._update(feature, status=new_status, **extra_fields)

    # ── Task #115: Idea auto-completion ─────────────────────────────
    def _maybe_complete_idea(self, idea_id: int) -> None:
        """Check if all FRs under an Idea are completed; if so, mark Idea as completed."""
        try:
            features = self.storage.list_features(limit=500)
            idea_features = [f for f in features if getattr(f, "idea_id", None) == idea_id]
            if not idea_features:
                return
            terminal_statuses = ("verified", "deployed", "rejected")
            all_done = all(f.status in terminal_statuses for f in idea_features)
            if all_done:
                self.storage.update_idea_status(idea_id, "completed")
                logger.info("Idea #%d auto-completed: all %d FRs done", idea_id, len(idea_features))
        except NotImplementedError:
            pass  # storage backend doesn't support ideas yet
        except Exception as e:  # pragma: no cover
            logger.debug("_maybe_complete_idea(%d) failed: %s", idea_id, e)

    # ── Task #119: data-driven verification helper ────────────────────
    def _run_data_driven_verification(
        self, feature: FeatureRequest,
    ) -> Optional[VerificationVerdict]:
        """Run data-driven verification using metrics comparison.

        Attempts to collect current metrics and compare against the baseline
        stored on the feature. Returns None if no baseline is available or
        if no commands are configured (graceful skip).
        """
        try:
            # Build collector config from harness-like fields on the pipeline config
            harness_cfg = {
                "test_command": self.config.quality_test_command or "",
                "lint_command": "",  # Not on pipeline config; skip
                "typecheck_command": "",
                "build_command": "",
            }
            # If no test command is configured, data-driven cannot collect anything
            if not any(harness_cfg.values()):
                return None

            collector = MetricsCollector(
                workspace=self.worktrees.repo_root,
                config=harness_cfg,
            )

            # Retrieve stored baseline from feature metadata
            baseline_data = getattr(feature, "_metrics_baseline", None)
            if baseline_data is None:
                # No baseline stored — collect one now as reference
                # (first-time run for this feature)
                return None

            baseline = MetricSnapshot(**baseline_data) if isinstance(baseline_data, dict) else baseline_data

            verifier = DataDrivenVerifier(
                collector=collector,
                thresholds=self.config.verification_thresholds,
            )
            return verifier.verify(baseline)
        except Exception as exc:
            logger.debug(
                "Data-driven verification failed for #%s: %s",
                feature.id, exc,
            )
            return None

    def _collect_baseline_metrics(self, feature: FeatureRequest) -> Optional[MetricSnapshot]:
        """Collect baseline metrics before development starts.

        Returns the snapshot or None if collection is not possible.
        """
        try:
            harness_cfg = {
                "test_command": self.config.quality_test_command or "",
                "lint_command": "",
                "typecheck_command": "",
                "build_command": "",
            }
            if not any(harness_cfg.values()):
                return None

            collector = MetricsCollector(
                workspace=self.worktrees.repo_root,
                config=harness_cfg,
            )
            snapshot = collector.collect_snapshot()
            # Stash on feature object for verify stage to use
            object.__setattr__(feature, "_metrics_baseline", snapshot)
            return snapshot
        except Exception as exc:
            logger.debug(
                "Baseline metrics collection failed for #%s: %s",
                feature.id, exc,
            )
            return None

    # ── Fix #69: pre-push change detection helper ─────────────────────────
    @staticmethod
    def _has_actual_changes(worktree_path, base_ref: str = "origin/main") -> bool:
        """Check whether the worktree branch has commits relative to *base_ref*."""
        try:
            result = subprocess.run(
                ["git", "log", f"{base_ref}..HEAD", "--oneline"],
                capture_output=True, text=True, timeout=10,
                cwd=str(worktree_path),
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False  # assume no changes on error to avoid invalid PRs

    # ── Task #129: per-round HEAD sha helper ─────────────────────────
    @staticmethod
    def _get_head_sha(worktree_path) -> str:
        """Return the worktree's current HEAD commit sha (empty string on error).

        Used to compare HEAD before/after an agent run so we can detect
        whether *this round* produced any new commits — independent of
        residual commits that may exist on a reused worktree.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=str(worktree_path),
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    # ── Fix #69: recover deployed_with_issues caused by PR failures ───────
    def _recover_failed_deployments(self) -> None:
        """Reset features stuck in deployed_with_issues due to PR creation failures.

        Features that ended up in this state because the branch had no new
        commits (AI agent didn't produce changes) or because of transient PR
        errors are rolled back to ``approved`` so the next round retries them.
        Features that exhausted their retry budget are auto-rejected.
        """
        try:
            stuck = self.storage.list_features(status="deployed_with_issues")
        except Exception as e:  # pragma: no cover
            logger.debug("list_features(deployed_with_issues) failed: %s", e)
            return

        # Keywords indicating a PR/push failure (not a real functionality issue)
        _RECOVERABLE_KEYWORDS = (
            "No commits",
            "no new commit",
            "PR creation failed",
            "push_and_open",
            "git push failed",
            "Remote session failed",
        )

        for f in stuck:
            dev_result = getattr(f, "development_result", "") or ""
            if not any(kw.lower() in dev_result.lower() for kw in _RECOVERABLE_KEYWORDS):
                continue  # likely a real quality/functionality issue; leave it

            new_retry = int(getattr(f, "retry_count", 0) or 0) + 1
            if new_retry >= self.config.max_retries:
                logger.info(
                    "Feature #%d: max retries (%d) reached, rejecting",
                    f.id, self.config.max_retries,
                )
                # Task #74: 尝试自动 revert 已合并的代码
                self._maybe_auto_revert(f)
                if f.status != "rejected":
                    self._update(
                        f,
                        status="rejected",
                        retry_count=new_retry,
                        feasibility=(
                            f"Auto-rejected: PR creation failed {new_retry} times"
                        ),
                    )
                continue

            logger.info(
                "Feature #%d: recovering from PR failure, "
                "rolling back to approved (retry %d/%d)",
                f.id, new_retry, self.config.max_retries,
            )
            self._update(f, status="approved", retry_count=new_retry)

    # ── Task #74: auto-revert for failed features ──────────────────────────
    def _maybe_auto_revert(self, feature: FeatureRequest) -> None:
        """检查是否需要自动 revert 已合并的代码。

        当 feature 验证失败且重试超限、且存在 commit_hash 时，
        自动创建 revert PR 撤回代码。
        """
        if not self.config.auto_revert_enabled:
            return
        if not getattr(feature, "commit_hash", None):
            return
        retry_count = int(getattr(feature, "retry_count", 0) or 0)
        if retry_count < self.config.max_verify_retries:
            return

        logger.info(
            "Feature #%s: triggering auto-revert for commit %s",
            feature.id, feature.commit_hash,
        )
        import os
        reverter = AutoReverter(
            repo_path=str(self.worktrees.repo_root),
            base_branch=self.pr_creator.config.base_branch,
            env=os.environ.copy(),
        )
        result = reverter.revert_commit(
            commit_hash=feature.commit_hash,
            feature_title=feature.title or f"feature-{feature.id}",
            feature_id=feature.id,
        )

        prev_result = getattr(feature, "development_result", None) or ""
        if result.success:
            new_result = prev_result + f"\n[auto-revert] PR: {result.revert_pr_url}"
        else:
            new_result = prev_result + f"\n[auto-revert failed] {result.error}"

        # 标记为 rejected
        self._update(
            feature,
            status="rejected",
            development_result=new_result[-2000:],
            feasibility=(
                f"Auto-rejected + reverted: verification failed "
                f"after {retry_count} retries"
            ),
        )

    # ── Task #61: timeout detection & auto-recovery ────────────────────────
    def _detect_and_recover_timeouts(self) -> None:
        """Reset features stuck in transient states longer than the threshold.

        Iterates over features in ``evaluating`` / ``developing`` / ``verifying``
        and, when their start timestamp is older than the configured threshold,
        bumps ``retry_count`` and rolls them back to a recoverable status.
        Once ``retry_count`` reaches ``config.max_retries`` the feature is
        auto-rejected so it stops blocking the pipeline.
        """
        now = datetime.now(timezone.utc)
        thresholds: dict[str, timedelta] = {
            "evaluating": timedelta(minutes=self.config.evaluating_timeout_minutes),
            "developing": timedelta(minutes=self.config.developing_timeout_minutes),
            "verifying": timedelta(minutes=self.config.verifying_timeout_minutes),
        }
        # Roll-back targets: which status to reset a stuck feature to.
        recovery_status: dict[str, str] = {
            "evaluating": "pending",
            "developing": "approved",
            "verifying": "deployed",
        }

        for status, threshold in thresholds.items():
            try:
                stuck = self.storage.list_features(status=status)
            except Exception as e:  # pragma: no cover
                logger.debug("list_features(%s) failed: %s", status, e)
                continue

            for f in stuck:
                started = self._extract_status_start_time(f, status)
                if started is None:
                    continue
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if now - started <= threshold:
                    continue

                new_retry = int(getattr(f, "retry_count", 0) or 0) + 1
                if new_retry >= self.config.max_retries:
                    self._update(
                        f,
                        status="rejected",
                        retry_count=new_retry,
                        feasibility=(
                            f"Auto-rejected: timed out {new_retry} times in '{status}'"
                        ),
                    )
                    final_status = "rejected"
                else:
                    final_status = recovery_status[status]
                    self._update(f, status=final_status, retry_count=new_retry)

                logger.warning(
                    "Feature #%d timeout in '%s' (started %s), reset to '%s' "
                    "(retry %d/%d)",
                    f.id, status, started.isoformat(), final_status,
                    new_retry, self.config.max_retries,
                )

    @staticmethod
    def _extract_status_start_time(
        f: FeatureRequest, status: str,
    ) -> Optional[datetime]:
        """Best-effort start timestamp for the given transient status."""
        if status == "evaluating":
            candidates: list = [f.evaluated_at, f.created_at]
        elif status == "developing":
            candidates = [f.started_at, f.evaluated_at, f.created_at]
        elif status == "verifying":
            candidates = [f.verified_at, f.started_at, f.created_at]
        else:  # pragma: no cover
            candidates = [f.created_at]
        for c in candidates:
            if c is None or c == "":
                continue
            if isinstance(c, datetime):
                return c
            try:
                return datetime.fromisoformat(str(c).rstrip("Z"))
            except (ValueError, TypeError):
                continue
        return None

    def _log_action(self, **kw) -> None:
        feature: FeatureRequest = kw.pop("feature")
        try:
            self.storage.log_action(
                ActionLog(
                    run_id=self.run_id,
                    round_num=kw.pop("round_num", 0),
                    action_type=kw.pop("action_type"),
                    status=kw.pop("status", "running"),
                    category=feature.type,
                    title=feature.title,
                    result_summary=kw.pop("summary", ""),
                    details={"feature_id": feature.id, **kw.pop("details", {})},
                    duration_seconds=kw.pop("duration", None),
                    pr_url=kw.pop("pr_url", None),
                    commit_hash=kw.pop("commit_hash", None),
                )
            )
        except Exception as e:  # pragma: no cover
            logger.debug("log_action failed: %s", e)

    @staticmethod
    def _render_pr_body(
        feature: FeatureRequest,
        *,
        output: str,
        quality: QualityCheckResult,
    ) -> str:
        return (
            f"## vivify feature #{feature.id}: {feature.title}\n\n"
            f"**Type**: `{feature.type}`  **Priority**: `{feature.priority or '?'}`\n\n"
            f"### Description\n{feature.description or '(no description)'}\n\n"
            f"### Quality\n```\n{quality.summary or '(no quality report)'}\n```\n\n"
            f"### Agent output (tail)\n```\n{(output or '')[-1500:]}\n```\n"
        )


__all__ = [
    "AgentCostModel",
    "FeaturePipeline",
    "FeaturePipelineConfig",
    "FeatureRunReport",
]
