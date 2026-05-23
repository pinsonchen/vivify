"""``lint_autofix`` — apply ruff/eslint ``--fix`` for the lint subset of issues."""
from __future__ import annotations

import time

from vivify.fixers.base import (
    BaseFixer, fail, has_command, list_changed_files, run_cmd, stage_and_commit, success,
)
from vivify.interfaces.fixer import FixContext
from vivify.models import FixResult, Issue


class LintAutofix(BaseFixer):
    id = "lint_autofix"
    description = "Run ruff --fix / eslint --fix to clear the easy lint findings."
    handles_categories = ("lint_typecheck",)

    def can_fix(self, issue: Issue, ctx: FixContext) -> bool:
        if issue.category not in self.handles_categories:
            return False
        # Need at least one of the supported linters.
        return has_command("ruff") or has_command("npx")

    def fix(self, issue: Issue, ctx: FixContext) -> FixResult:
        ws = ctx.workspace or ctx.repo_root
        t0 = time.time()
        ran_any = False

        if has_command("ruff"):
            res = run_cmd(["ruff", "check", "--fix", "."], cwd=ws, timeout=300)
            ran_any = True
            ctx.logger.info("[lint_autofix] ruff exit=%d", res.returncode)

        if has_command("npx") and (ws / "package.json").exists():
            res = run_cmd(["npx", "--no-install", "eslint", ".", "--fix"], cwd=ws, timeout=300)
            ran_any = True
            ctx.logger.info("[lint_autofix] eslint exit=%d", res.returncode)

        if not ran_any:
            return fail("no supported linter on PATH", duration=time.time() - t0)

        changed = list_changed_files(ws)
        if not changed:
            return fail("linter ran but made no changes", duration=time.time() - t0)

        ok, info = stage_and_commit(
            ws, paths=changed, message=f"vivify: lint_autofix ({len(changed)} files)"
        )
        if not ok:
            return fail(info, duration=time.time() - t0)
        return success(
            message=f"applied lint fixes to {len(changed)} files",
            changed_files=changed,
            duration=time.time() - t0,
            commit_hash=info,
        )


FIXER = LintAutofix()
