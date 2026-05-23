"""``ci_cache_clear`` — purge old GitHub Actions caches when the build slows down."""
from __future__ import annotations

import json
import time

from vivify.fixers.base import BaseFixer, fail, has_command, run_cmd, success
from vivify.interfaces.fixer import FixContext
from vivify.models import FixResult, Issue


class CiCacheClear(BaseFixer):
    id = "ci_cache_clear"
    description = "Delete stale GitHub Actions caches to recover from cache bloat."
    handles_categories = ("ci_build_slowdown",)

    def can_fix(self, issue: Issue, ctx: FixContext) -> bool:
        return issue.category in self.handles_categories and has_command("gh")

    def fix(self, issue: Issue, ctx: FixContext) -> FixResult:
        ws = ctx.workspace or ctx.repo_root
        t0 = time.time()
        listing = run_cmd(
            ["gh", "cache", "list", "--limit", "100", "--json", "id,key,sizeInBytes,createdAt"],
            cwd=ws, timeout=60,
        )
        if listing.returncode != 0:
            return fail(f"gh cache list failed: {listing.stderr[:200]}", duration=time.time() - t0)
        try:
            entries = json.loads(listing.stdout or "[]")
        except json.JSONDecodeError:
            return fail("gh cache list returned non-JSON", duration=time.time() - t0)
        if not entries:
            return fail("no GH Actions caches to delete", duration=time.time() - t0)

        # Conservative: delete the oldest 25% (capped at 25 entries).
        entries.sort(key=lambda e: e.get("createdAt", ""))
        victims = entries[: max(1, min(25, len(entries) // 4))]
        deleted = 0
        for v in victims:
            res = run_cmd(["gh", "cache", "delete", str(v["id"])], cwd=ws, timeout=30)
            if res.returncode == 0:
                deleted += 1

        if not deleted:
            return fail("could not delete any caches", duration=time.time() - t0)
        return success(
            message=f"deleted {deleted} old GH Actions cache entries",
            changed_files=[],
            duration=time.time() - t0,
            artifacts={"deleted_ids": [v["id"] for v in victims[:deleted]]},
        )


FIXER = CiCacheClear()
