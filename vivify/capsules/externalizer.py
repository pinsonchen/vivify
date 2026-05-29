"""Capability externalization - transforms Vivify knowledge into native project abilities.

Lifecycle:
    SkillCapsule success >= 3 → promotion_candidate
      → generate ExternalizationPlan (CI step / hook / script)
      → write to .vivify/externalized/
      → success >= 5 → archive capsule (project already has this ability)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from vivify.capsules.models import SkillCapsule
from vivify.capsules.store import CapsuleStore

logger = logging.getLogger(__name__)


@dataclass
class ExternalizationPlan:
    """A plan to externalize a skill capsule into native project capability."""

    capsule_id: str
    capsule_title: str
    target_type: str        # github_action / pre_commit_hook / makefile / script
    file_path: str          # 目标文件路径
    content: str            # 生成的内容
    description: str        # 人可读描述

    @property
    def commit_message(self) -> str:
        return f"chore: externalize '{self.capsule_title}' as {self.target_type}"


class CapabilityExternalizer:
    """Generates externalization plans from promoted capsules.

    The externalizer does NOT modify project files directly — it writes
    suggestion files into ``output_dir`` and leaves adoption to the user.
    """

    def __init__(self, project_root: Path, output_dir: Optional[str] = None):
        self._project_root = project_root
        self._output_dir = Path(output_dir) if output_dir else project_root / ".vivify" / "externalized"

    def generate_plan(self, capsule: SkillCapsule) -> Optional[ExternalizationPlan]:
        """Generate an externalization plan for a promoted capsule.

        Selects the best target type based on capsule characteristics.
        Returns ``None`` if the capsule cannot be externalized.
        """
        target_type = self._select_target_type(capsule)

        generators = {
            "github_action": self._generate_github_action,
            "pre_commit_hook": self._generate_pre_commit_hook,
            "makefile": self._generate_makefile_target,
            "script": self._generate_script,
        }

        generator = generators.get(target_type)
        if generator is None:
            return None

        return generator(capsule)

    def get_promotion_candidates(self, capsule_store: CapsuleStore) -> List[SkillCapsule]:
        """Get capsules ready for externalization."""
        return capsule_store.get_promotion_candidates()

    def write_plan(self, plan: ExternalizationPlan) -> Path:
        """Write an externalization plan to disk.

        Returns the path of the written file.
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        target = self._output_dir / Path(plan.file_path).name
        target.write_text(plan.content, encoding="utf-8")
        logger.info(
            "Externalization plan written: %s → %s",
            plan.capsule_title, target,
        )
        return target

    def run(self, capsule_store: CapsuleStore) -> List[ExternalizationPlan]:
        """Full externalization pass: find candidates, generate plans, write files.

        Returns the list of generated plans.
        """
        candidates = self.get_promotion_candidates(capsule_store)
        if not candidates:
            return []

        plans: List[ExternalizationPlan] = []
        for capsule in candidates:
            plan = self.generate_plan(capsule)
            if plan is None:
                continue
            self.write_plan(plan)
            plans.append(plan)
            # Mark capsule as promoted
            capsule.status = "promoted"
            capsule.promoted_to = plan.target_type
            capsule_store.save(capsule)
            logger.info(
                "Capsule '%s' promoted to %s",
                capsule.trigger_pattern, plan.target_type,
            )

        return plans

    # ── target type selection ─────────────────────────────────────────────

    def _select_target_type(self, capsule: SkillCapsule) -> str:
        """Select best externalization target based on capsule type.

        Logic:
        - lint/format related → pre_commit_hook
        - CI/test related → github_action
        - build related → makefile
        - other → script
        """
        category = (capsule.issue_category or "").lower()
        strategy = (capsule.fix_strategy or "").lower()
        combined = category + " " + strategy

        if any(kw in combined for kw in ("lint", "format", "style")):
            return "pre_commit_hook"
        elif any(kw in combined for kw in ("test", "ci", "coverage")):
            return "github_action"
        elif any(kw in combined for kw in ("build", "compile", "bundle")):
            return "makefile"
        else:
            return "script"

    # ── generators ────────────────────────────────────────────────────────

    def _generate_github_action(self, capsule: SkillCapsule) -> ExternalizationPlan:
        """Generate a GitHub Actions workflow step."""
        strategy = capsule.fix_strategy or "automated check"
        probe_id = capsule.probe_id or "check"

        content = (
            f"# Auto-generated by Vivify externalization\n"
            f"# Source: capsule {capsule.capsule_id}\n"
            f"name: {probe_id}-check\n"
            f"\n"
            f"on: [push, pull_request]\n"
            f"\n"
            f"jobs:\n"
            f"  check:\n"
            f"    runs-on: ubuntu-latest\n"
            f"    steps:\n"
            f"      - uses: actions/checkout@v4\n"
            f"      - name: {strategy[:50]}\n"
            f"        run: |\n"
            f"          # {strategy}\n"
            f'          echo "Implement: {strategy}"\n'
        )

        return ExternalizationPlan(
            capsule_id=capsule.capsule_id,
            capsule_title=capsule.trigger_pattern or probe_id,
            target_type="github_action",
            file_path=f".github/workflows/vivify-{probe_id}.yml",
            content=content,
            description=f"GitHub Action for {probe_id}: {strategy[:100]}",
        )

    def _generate_pre_commit_hook(self, capsule: SkillCapsule) -> ExternalizationPlan:
        """Generate a pre-commit hook config entry."""
        strategy = capsule.fix_strategy or "lint check"
        probe_id = capsule.probe_id or "lint"

        content = (
            f"# Auto-generated by Vivify externalization\n"
            f"# Source: capsule {capsule.capsule_id}\n"
            f"# Add to .pre-commit-config.yaml:\n"
            f"repos:\n"
            f"  - repo: local\n"
            f"    hooks:\n"
            f"      - id: vivify-{probe_id}\n"
            f"        name: {strategy[:50]}\n"
            f"        entry: bash -c '{strategy}'\n"
            f"        language: system\n"
            f"        always_run: true\n"
        )

        return ExternalizationPlan(
            capsule_id=capsule.capsule_id,
            capsule_title=capsule.trigger_pattern or probe_id,
            target_type="pre_commit_hook",
            file_path=f".vivify/externalized/{probe_id}-hook.yml",
            content=content,
            description=f"Pre-commit hook for {probe_id}: {strategy[:100]}",
        )

    def _generate_makefile_target(self, capsule: SkillCapsule) -> ExternalizationPlan:
        """Generate a Makefile target."""
        strategy = capsule.fix_strategy or "build step"
        probe_id = capsule.probe_id or "build"

        content = (
            f"# Auto-generated by Vivify externalization\n"
            f"# Source: capsule {capsule.capsule_id}\n"
            f"# Add to Makefile:\n"
            f"\n"
            f".PHONY: vivify-{probe_id}\n"
            f"vivify-{probe_id}:  ## {strategy[:60]}\n"
            f"\t@echo \"Running {probe_id} check...\"\n"
            f"\t# {strategy}\n"
        )

        return ExternalizationPlan(
            capsule_id=capsule.capsule_id,
            capsule_title=capsule.trigger_pattern or probe_id,
            target_type="makefile",
            file_path=f".vivify/externalized/{probe_id}-make.mk",
            content=content,
            description=f"Makefile target for {probe_id}: {strategy[:100]}",
        )

    def _generate_script(self, capsule: SkillCapsule) -> ExternalizationPlan:
        """Generate a shell script."""
        strategy = capsule.fix_strategy or "automated fix"
        probe_id = capsule.probe_id or "fix"

        content = (
            f"#!/usr/bin/env bash\n"
            f"# Auto-generated by Vivify externalization\n"
            f"# Source: capsule {capsule.capsule_id}\n"
            f"# Strategy: {strategy}\n"
            f"\n"
            f"set -euo pipefail\n"
            f"\n"
            f'echo "Running externalized fix: {probe_id}"\n'
            f"# {strategy}\n"
        )

        return ExternalizationPlan(
            capsule_id=capsule.capsule_id,
            capsule_title=capsule.trigger_pattern or probe_id,
            target_type="script",
            file_path=f".vivify/externalized/{probe_id}-fix.sh",
            content=content,
            description=f"Script for {probe_id}: {strategy[:100]}",
        )


__all__ = ["CapabilityExternalizer", "ExternalizationPlan"]
