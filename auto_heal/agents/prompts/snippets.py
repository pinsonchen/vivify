"""Reusable prompt snippets — project-agnostic, parameterised at render time.

These replace channels-monitor's ``AUTH_SNIPPET`` / ``COMMIT_SNIPPET`` /
``NEXT_STEPS_SNIPPET`` / ``SELF_IMPROVE_SNIPPET`` constants. The original
versions were tightly coupled to that project's URLs and credentials; here the
deploy story is unified PR mode, and the auth snippet is only injected when a
probe explicitly declares ``auth_required: true``.
"""
from __future__ import annotations

from textwrap import dedent
from typing import Mapping, Optional


# ── PR / git workflow ────────────────────────────────────────────────────────
GIT_PR_SNIPPET = dedent(
    """
    ## Land your changes via Pull Request

    auto-heal is in PR-only mode. **Never push directly to the base branch.**
    Instead:

    1. Stage and commit your edits inside the current worktree:
       ```bash
       git add -A
       git commit -m "auto-heal: <one-line summary>"
       ```
    2. Push the branch (auto-heal already created it for you):
       ```bash
       git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
       ```
    3. The auto-heal kernel will open the Pull Request via the GitHub CLI
       after this run completes; you do not need to call `gh pr create`
       yourself.

    If you made no code changes (only ran read-only commands), skip steps 1–3.
    """
).strip()


# ── Auth (opt-in; rendered only when probe declares auth_required) ───────────
def auth_snippet(*, auth_command: str, verify_command: Optional[str] = None) -> str:
    """Render a parameterised auth block for probes that need credentials.

    ``auth_command`` is a shell command supplied by the probe configuration
    (e.g. ``aws sso login`` or ``vault login -method=oidc``). We deliberately
    do not bake any URLs or hard-coded credentials.
    """
    parts = [
        "## Authentication (run before any privileged commands)",
        "```bash",
        auth_command.strip(),
        "```",
    ]
    if verify_command:
        parts.append("\nVerify with:")
        parts.append("```bash")
        parts.append(verify_command.strip())
        parts.append("```")
    return "\n".join(parts).strip()


# ── Follow-up suggestions emitted by the develop / fix prompts ───────────────
NEXT_STEPS_SNIPPET = dedent(
    """
    ## Follow-up suggestions (always emit, even if empty)

    If this task is complex and you only completed part of it this round, list
    independent follow-up steps as a final JSON block so the kernel can pick
    them up in subsequent rounds:

    ```json
    {
      "next_steps": [
        {
          "title": "Short title (≤50 chars)",
          "description": "Why it's needed, what to do, expected outcome (100-300 chars)."
        }
      ]
    }
    ```

    Rules:

    - If the task is fully done, emit `{"next_steps": []}`.
    - Each item must be independently actionable.
    - Maximum 3 items, ranked by importance.
    - Place the JSON block at the very end of your output (after any commit /
      verification logs) so the parser can find it.
    - Do NOT list things you already finished — only future work that would
      need a fresh round.
    """
).strip()


# ── Self-growth (opt-in; gated by self_growth.enabled in config) ────────────
SELF_IMPROVE_SNIPPET = dedent(
    """
    ## Self-improvement (optional)

    You may modify the auto-heal package itself to make future runs smarter,
    *within these whitelisted paths only*:

    - `auto_heal/probes/builtin/**` — tweak detection rules / thresholds
    - `auto_heal/fixers/builtin/**` — add or improve deterministic fixes
    - `auto_heal/agents/prompts/templates/**` — improve prompt phrasing
    - `auto_heal/agents/prompts/snippets.py` — improve reusable snippets

    Any change outside this whitelist is treated as a kernel modification:
    the resulting PR will be marked draft, will not auto-merge, and will
    require human review (see `pr_mode/self_grow_guard.py`).

    Guidelines:

    - Mirror the existing code style.
    - Guard new behaviour behind a config flag when it could regress users.
    - Add or update unit tests in `tests/unit/` for any behaviour change.
    """
).strip()


# ── Convenience: render a category-specific remediation hint ─────────────────
def remediation_hint(*, category: str, hints: Mapping[str, str]) -> str:
    """Look up an optional ``remediation_hint`` declared by a probe.

    The mapping comes from the YAML probe definition; missing → empty string.
    """
    text = hints.get(category) if hints else None
    if not text:
        return ""
    return f"## Recommended remediation (from probe)\n{text.strip()}"


__all__ = [
    "GIT_PR_SNIPPET",
    "NEXT_STEPS_SNIPPET",
    "SELF_IMPROVE_SNIPPET",
    "auth_snippet",
    "remediation_hint",
]
