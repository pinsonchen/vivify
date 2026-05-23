"""Prompt building blocks: snippets, templates, builders, and parsers.

The public surface is intentionally narrow — kernel modules import
``builders.build_*`` and ``parsers.parse_*`` and never reach into the
templates directly.
"""
from auto_heal.agents.prompts.builders import (
    build_feature_develop,
    build_feature_evaluate,
    build_feature_verify,
    build_fix_issue,
    build_goal_decompose,
)
from auto_heal.agents.prompts.parsers import (
    parse_commit_info,
    parse_evaluation_result,
    parse_next_steps,
    parse_skipped_ids,
    parse_verification_result,
)
from auto_heal.agents.prompts.snippets import (
    GIT_PR_SNIPPET,
    NEXT_STEPS_SNIPPET,
    SELF_IMPROVE_SNIPPET,
    auth_snippet,
    remediation_hint,
)

__all__ = [
    # builders
    "build_fix_issue",
    "build_feature_evaluate",
    "build_feature_develop",
    "build_feature_verify",
    "build_goal_decompose",
    # parsers
    "parse_evaluation_result",
    "parse_verification_result",
    "parse_next_steps",
    "parse_skipped_ids",
    "parse_commit_info",
    # snippets
    "GIT_PR_SNIPPET",
    "NEXT_STEPS_SNIPPET",
    "SELF_IMPROVE_SNIPPET",
    "auth_snippet",
    "remediation_hint",
]
