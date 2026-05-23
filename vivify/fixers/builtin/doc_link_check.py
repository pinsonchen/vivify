"""``doc_link_check`` — run lychee on markdown to detect broken links."""
from __future__ import annotations

import time

from vivify.fixers.base import BaseFixer, fail, has_command, run_cmd, success
from vivify.interfaces.fixer import FixContext
from vivify.models import FixResult, Issue


class DocLinkCheck(BaseFixer):
    """Detects broken links in docs and reports them; auto-fixing links is out of
    scope (link replacement requires semantic judgement), but surfacing them
    upgrades the issue with concrete URLs the agent can act on."""

    id = "doc_link_check"
    description = "Scan README / docs for broken links with lychee."
    handles_categories = ("doc_staleness", "doc_broken_links")

    def can_fix(self, issue: Issue, ctx: FixContext) -> bool:
        return issue.category in self.handles_categories and has_command("lychee")

    def fix(self, issue: Issue, ctx: FixContext) -> FixResult:
        ws = ctx.workspace or ctx.repo_root
        t0 = time.time()
        res = run_cmd(
            ["lychee", "--no-progress", "--max-redirects", "3", "**/*.md"],
            cwd=ws, timeout=300, shell=False,
        )
        # lychee exit 0 = all good; 1 = broken found; 2 = config/runtime error.
        if res.returncode == 0:
            return success(
                message="lychee found no broken links",
                changed_files=[],
                duration=time.time() - t0,
            )
        if res.returncode == 2:
            return fail(f"lychee runtime error: {res.stderr[:200]}", duration=time.time() - t0)

        broken_lines = [ln for ln in (res.stdout or "").splitlines() if "ERROR" in ln or "✗" in ln][:20]
        return fail(
            "lychee detected broken links (manual / agent action required):\n"
            + "\n".join(broken_lines),
            duration=time.time() - t0,
        )


FIXER = DocLinkCheck()
