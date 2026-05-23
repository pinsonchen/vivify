"""``stale_branch_prune`` — delete merged remote branches that have aged out."""
from __future__ import annotations

import time

from vivify.fixers.base import BaseFixer, fail, has_command, run_cmd, success
from vivify.interfaces.fixer import FixContext
from vivify.models import FixResult, Issue


class StaleBranchPrune(BaseFixer):
    id = "stale_branch_prune"
    description = "Delete remote branches that are already merged into the default branch."
    handles_categories = ("stale_branches",)

    # Branches we never auto-delete regardless of merge status.
    PROTECTED = ("main", "master", "develop", "release")

    def can_fix(self, issue: Issue, ctx: FixContext) -> bool:
        return issue.category in self.handles_categories and has_command("gh")

    def fix(self, issue: Issue, ctx: FixContext) -> FixResult:
        ws = ctx.workspace or ctx.repo_root
        t0 = time.time()
        # Discover merged branches off origin/main (assume default = main).
        merged = run_cmd(
            ["git", "branch", "-r", "--merged", "origin/main"],
            cwd=ws, timeout=30,
        )
        if merged.returncode != 0:
            return fail(f"git branch -r --merged failed: {merged.stderr[:200]}", duration=time.time() - t0)

        candidates: list[str] = []
        for raw in (merged.stdout or "").splitlines():
            name = raw.strip()
            if not name.startswith("origin/"):
                continue
            short = name[len("origin/"):]
            if short in self.PROTECTED or short.startswith("HEAD"):
                continue
            candidates.append(short)

        if not candidates:
            return fail("no merged branches eligible for pruning", duration=time.time() - t0)

        deleted: list[str] = []
        for br in candidates[:20]:  # cap per run
            res = run_cmd(["gh", "api", "-X", "DELETE", f"repos/:owner/:repo/git/refs/heads/{br}"],
                          cwd=ws, timeout=20)
            if res.returncode == 0:
                deleted.append(br)
            else:
                ctx.logger.info("[stale_branch_prune] skip %s: %s", br, res.stderr.strip()[:120])
        if not deleted:
            return fail("no branches deleted (likely auth or permissions)", duration=time.time() - t0)
        return success(
            message=f"deleted {len(deleted)} merged remote branches",
            changed_files=[],
            duration=time.time() - t0,
            artifacts={"deleted": deleted},
        )


FIXER = StaleBranchPrune()
