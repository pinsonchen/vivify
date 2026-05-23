"""``format_autofix`` — run black / prettier on the worktree."""
from __future__ import annotations

import time

from auto_heal.fixers.base import (
    BaseFixer, fail, has_command, list_changed_files, run_cmd, stage_and_commit, success,
)
from auto_heal.interfaces.fixer import FixContext
from auto_heal.models import FixResult, Issue


class FormatAutofix(BaseFixer):
    id = "format_autofix"
    description = "Run black / prettier to normalise code style."
    handles_categories = ("formatting", "lint_typecheck")

    def can_fix(self, issue: Issue, ctx: FixContext) -> bool:
        if issue.category not in self.handles_categories:
            return False
        return has_command("black") or has_command("npx") or has_command("prettier")

    def fix(self, issue: Issue, ctx: FixContext) -> FixResult:
        ws = ctx.workspace or ctx.repo_root
        t0 = time.time()
        ran_any = False

        if has_command("black") and (ws / "pyproject.toml").exists():
            res = run_cmd(["black", "."], cwd=ws, timeout=300)
            ran_any = True
            ctx.logger.info("[format_autofix] black exit=%d", res.returncode)

        if (ws / "package.json").exists():
            if has_command("prettier"):
                cmd = ["prettier", "--write", "."]
            elif has_command("npx"):
                cmd = ["npx", "--no-install", "prettier", "--write", "."]
            else:
                cmd = None
            if cmd:
                res = run_cmd(cmd, cwd=ws, timeout=300)
                ran_any = True
                ctx.logger.info("[format_autofix] prettier exit=%d", res.returncode)

        if not ran_any:
            return fail("no supported formatter on PATH", duration=time.time() - t0)

        changed = list_changed_files(ws)
        if not changed:
            return fail("formatter ran but made no changes", duration=time.time() - t0)
        ok, info = stage_and_commit(
            ws, paths=changed, message=f"auto-heal: format_autofix ({len(changed)} files)"
        )
        if not ok:
            return fail(info, duration=time.time() - t0)
        return success(
            message=f"reformatted {len(changed)} files",
            changed_files=changed,
            duration=time.time() - t0,
            commit_hash=info,
        )


FIXER = FormatAutofix()
