"""``dependency_bump`` — bump vulnerable packages via pip-tools / npm update."""
from __future__ import annotations

import time

from vivify.fixers.base import (
    BaseFixer, fail, has_command, list_changed_files, run_cmd, stage_and_commit, success,
)
from vivify.interfaces.fixer import FixContext
from vivify.models import FixResult, Issue


class DependencyBump(BaseFixer):
    id = "dependency_bump"
    description = "Bump dependencies (pip-compile -U / npm update) to clear vulnerabilities."
    handles_categories = ("dependency_vulnerabilities", "outdated_dep")

    def can_fix(self, issue: Issue, ctx: FixContext) -> bool:
        if issue.category not in self.handles_categories:
            return False
        ws = ctx.workspace or ctx.repo_root
        py = has_command("pip-compile") and (
            (ws / "requirements.in").exists() or (ws / "pyproject.toml").exists()
        )
        js = has_command("npm") and (ws / "package.json").exists()
        return py or js

    def fix(self, issue: Issue, ctx: FixContext) -> FixResult:
        ws = ctx.workspace or ctx.repo_root
        t0 = time.time()
        actions: list[str] = []

        if has_command("pip-compile") and (ws / "requirements.in").exists():
            res = run_cmd(["pip-compile", "-U", "requirements.in"], cwd=ws, timeout=300)
            actions.append(f"pip-compile -U → exit {res.returncode}")
            ctx.logger.info("[dependency_bump] pip-compile exit=%d", res.returncode)

        if has_command("npm") and (ws / "package.json").exists():
            # `npm update` respects semver in package.json; safer than `npm audit fix --force`.
            res = run_cmd(["npm", "update"], cwd=ws, timeout=600)
            actions.append(f"npm update → exit {res.returncode}")
            ctx.logger.info("[dependency_bump] npm update exit=%d", res.returncode)

        if not actions:
            return fail("no dependency manager available", duration=time.time() - t0)

        changed = list_changed_files(ws)
        if not changed:
            return fail("no lockfile / requirements changes produced", duration=time.time() - t0)

        ok, info = stage_and_commit(
            ws, paths=changed, message="vivify: bump dependencies",
        )
        if not ok:
            return fail(info, duration=time.time() - t0)
        return success(
            message="; ".join(actions),
            changed_files=changed,
            duration=time.time() - t0,
            commit_hash=info,
        )


FIXER = DependencyBump()
