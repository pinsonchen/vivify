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
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from vivify.agents.history import load_history
from vivify.agents.prompts import builders, parsers
from vivify.interfaces.agent import CodingAgent
from vivify.interfaces.storage import StorageProvider
from vivify.models.agent_result import AgentResult
from vivify.models.feature import FeatureRequest
from vivify.models.snapshot import ActionLog, KnowledgeEntry
from vivify.pr_mode.auto_merge import AutoMerge
from vivify.pr_mode.pr_creator import PrCreator, PullRequest
from vivify.pr_mode.quality_check import QualityCheckResult, run_quality_checks
from vivify.pr_mode.self_grow_guard import classify_worktree
from vivify.pr_mode.worktree import WorktreeManager

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────────
# Config + result types
# ────────────────────────────────────────────────────────────────────────────────


@dataclass
class FeaturePipelineConfig:
    max_turns_evaluate: int = 20
    max_turns_develop: int = 100
    max_turns_verify: int = 20
    timeout_evaluate_seconds: int = 600
    timeout_develop_seconds: int = 3600
    timeout_verify_seconds: int = 600
    quality_test_command: Optional[str] = None
    quality_run_pytest: bool = False
    repo_url: Optional[str] = None
    max_followups: int = 3
    # ── lifecycle timeouts (Task #61: stuck-feature auto-recovery) ─────────
    evaluating_timeout_minutes: int = 10
    developing_timeout_minutes: int = 90
    verifying_timeout_minutes: int = 60
    max_retries: int = 3


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
            category="feature_evaluate",
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
        slug = feature.title or f"feature-{feature.id}"
        wt = self.worktrees.create(slug)
        result: Optional[AgentResult] = None
        try:
            self._update(feature, status="developing")
            history = load_history(self.storage, "feature_develop")
            approach = getattr(feature, "_approach", "")
            prompt = builders.build_feature_develop(
                feature, workspace=str(wt.path),
                recent_history=history,
                implementation_approach=approach,
            )
            result = self.agent.heal(
                prompt,
                max_turns=self.config.max_turns_develop,
                category="feature_develop",
                workspace=wt.path,
                timeout_seconds=self.config.timeout_develop_seconds,
            )
            output = result.output or ""

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
            report.durations["develop"] = time.time() - start
            self._log_action(
                round_num=round_num, action_type="feature_develop",
                status="success" if report.status == "deployed" else "failed",
                feature=feature,
                summary=(result.output[-1000:] if result and result.output else "")[:2000],
                details={"followups": report.followups_created,
                         "pr_url": report.pr.url if report.pr else None},
                duration=report.durations["develop"],
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
        prompt = builders.build_feature_verify(feature)
        result = self.agent.heal(
            prompt,
            max_turns=self.config.max_turns_verify,
            category="feature_verify",
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

    def _update_feature_status(
        self, feature: FeatureRequest, new_status: str, **extra_fields,
    ) -> None:
        """Unified status transition with automatic timestamp recording.

        Thin wrapper around :meth:`_update` for callers that prefer an explicit
        intent. Existing direct ``_update(feature, status=...)`` calls keep
        working and still get timestamps via the same code path.
        """
        self._update(feature, status=new_status, **extra_fields)

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
    "FeaturePipeline",
    "FeaturePipelineConfig",
    "FeatureRunReport",
]
