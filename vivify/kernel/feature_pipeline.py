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

import logging
import time
from dataclasses import dataclass, field
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
        if not parsed.get("feasible", True):
            self._update(feature, status="rejected",
                         feasibility=feature.feasibility, summary=feature.summary)
            self._log_action(
                round_num=round_num,
                action_type="feature_evaluate",
                status="success",
                feature=feature,
                summary=feature.summary,
                details={"feasible": False, "approach": parsed.get("implementation_approach", "")},
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
        self._update(feature, status=new_status, summary=parsed.get("summary", feature.summary))

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

        report.durations["verify"] = time.time() - start
        report.status = new_status
        self._log_action(
            round_num=round_num, action_type="feature_verify",
            status="success" if verified else "failed",
            feature=feature, summary=parsed.get("summary", "")[:2000],
            details={"issues": parsed.get("issues", [])},
            duration=report.durations["verify"],
        )

    # ── helpers ────────────────────────────────────────────────────────────
    def _create_followups(self, parent: FeatureRequest, output: str) -> int:
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
        for k, v in fields.items():
            if hasattr(feature, k):
                setattr(feature, k, v)
        if feature.id:
            try:
                self.storage.update_feature(feature.id, **fields)
            except Exception as e:  # pragma: no cover
                logger.warning("update_feature(%s) failed: %s", feature.id, e)

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
