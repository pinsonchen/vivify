"""``test_flake_retry`` — re-run failing tests once; on persistent failure, surface."""
from __future__ import annotations

import time

from vivify.fixers.base import BaseFixer, fail, has_command, run_cmd, success
from vivify.interfaces.fixer import FixContext
from vivify.models import FixResult, Issue


class TestFlakeRetry(BaseFixer):
    id = "test_flake_retry"
    description = "Re-run failing pytest tests; report whether the re-run cleared them."
    handles_categories = ("test_flake", "test_failure")

    def can_fix(self, issue: Issue, ctx: FixContext) -> bool:
        return issue.category in self.handles_categories and has_command("pytest")

    def fix(self, issue: Issue, ctx: FixContext) -> FixResult:
        ws = ctx.workspace or ctx.repo_root
        t0 = time.time()
        # Re-run the previously failing tests only.
        res = run_cmd(["pytest", "--last-failed", "-q"], cwd=ws, timeout=900)
        if res.returncode == 0:
            return success(
                message="re-running last-failed tests passed cleanly (likely flake)",
                changed_files=[],
                duration=time.time() - t0,
                artifacts={"pytest_returncode": res.returncode},
            )
        return fail(
            f"re-run still failing (exit {res.returncode}); escalate to agent",
            duration=time.time() - t0,
        )


FIXER = TestFlakeRetry()
