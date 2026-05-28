"""Feedforward guides system for project-specific AI behavior constraints.

This module loads markdown guide files from ``.vivify/guides/`` and exposes
them as injectable prompt fragments that constrain agent behavior *before*
code modification (i.e. feedforward, in contrast to the harness sensors which
provide feedback *after* modification).

Guide files are matched to operation categories by filename prefix:
    always_*.md     → applied to every category
    fix_*.md        → applied during ``fix_issue``
    develop_*.md    → applied during ``develop_feature``
    verify_*.md     → applied during ``verify_feature``
    evaluate_*.md   → applied during ``evaluate_feature``
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Guide:
    """A single guide document loaded from disk."""

    name: str       # filename without extension
    category: str   # "always" | "fix" | "develop" | "verify" | "evaluate"
    content: str    # markdown content (already stripped)


class GuidesManager:
    """Manage project feedforward guide files.

    Loads ``.md`` files from ``.vivify/guides/`` directory. Files are matched
    to operation categories via filename prefix:

    - ``always_*.md`` → loaded for all categories
    - ``fix_*.md`` → loaded only during ``fix_issue``
    - ``develop_*.md`` → loaded only during ``develop_feature``
    - ``verify_*.md`` → loaded only during ``verify_feature``
    - ``evaluate_*.md`` → loaded only during ``evaluate_feature``
    """

    MAX_GUIDES_LENGTH = 1000  # max chars for combined guide text

    def __init__(self, guides_dir: Path):
        self._guides_dir = guides_dir
        self._guides: list[Guide] = []
        self._loaded = False

    # ─── Public API ───

    def load_guides(self) -> list[Guide]:
        """Load all ``.md`` files from the guides directory.

        Files are sorted by name for deterministic ordering. A missing
        directory is treated as an empty guide set (not an error).

        Returns:
            The list of successfully loaded :class:`Guide` instances.
        """
        self._guides = []

        if not self._guides_dir.exists():
            logger.debug("Guides directory not found: %s", self._guides_dir)
            self._loaded = True
            return self._guides

        md_files = sorted(self._guides_dir.glob("*.md"))

        for md_file in md_files:
            try:
                content = md_file.read_text(encoding="utf-8").strip()
                name = md_file.stem
                category = self._extract_category(name)
                self._guides.append(Guide(
                    name=name,
                    category=category,
                    content=content,
                ))
            except Exception as exc:
                logger.warning("Failed to load guide %s: %s", md_file, exc)

        self._loaded = True
        logger.info("Loaded %d guides from %s", len(self._guides), self._guides_dir)
        return self._guides

    def get_guides_for_category(self, category: str) -> str:
        """Get combined guide text applicable to the given category.

        Combines all ``always_*`` guides with category-specific guides into a
        single markdown string. The output is truncated to
        :data:`MAX_GUIDES_LENGTH` characters to bound prompt size.

        Args:
            category: The current operation category (e.g. ``"fix_issue"``,
                ``"develop_feature"``). Both raw and normalized forms are
                accepted.

        Returns:
            Combined markdown text of matching guides, or an empty string if
            no guides match.
        """
        if not self._loaded:
            self.load_guides()

        # Normalize category: "fix_issue" → "fix", "develop_feature" → "develop"
        normalized = self._normalize_category(category)

        matching = [
            guide for guide in self._guides
            if guide.category == "always" or guide.category == normalized
        ]

        if not matching:
            return ""

        # Combine guide contents with section headers; truncate when budget exceeded
        parts: list[str] = []
        total_len = 0
        for guide in matching:
            section = f"### {guide.name}\n{guide.content}"
            if total_len + len(section) > self.MAX_GUIDES_LENGTH:
                remaining = self.MAX_GUIDES_LENGTH - total_len - 20
                if remaining > 50:
                    parts.append(section[:remaining] + "\n[...truncated]")
                break
            parts.append(section)
            total_len += len(section) + 2  # +2 accounts for "\n\n" separator

        return "\n\n".join(parts)

    def generate_default_guides(self, project_info: dict) -> None:
        """Generate default guide files based on project information.

        Called during ``vivify init`` to seed the guides directory with a
        baseline set of policies:

        - ``always_code_style.md`` — generic code-style constraints
        - ``always_testing.md`` — test maintenance expectations
        - ``fix_scope.md`` — fix-scope discipline (avoid scope creep)

        Existing files are overwritten to keep guidance in sync with the
        latest project metadata.

        Args:
            project_info: Dict with optional keys ``language``,
                ``conventions``, ``test_framework`` used to specialize the
                rendered templates.
        """
        self._guides_dir.mkdir(parents=True, exist_ok=True)

        # always_code_style.md
        conventions = project_info.get("conventions", "")
        code_style_content = self._render_code_style_guide(conventions)
        (self._guides_dir / "always_code_style.md").write_text(
            code_style_content, encoding="utf-8"
        )

        # always_testing.md
        test_framework = project_info.get("test_framework", "")
        testing_content = self._render_testing_guide(test_framework)
        (self._guides_dir / "always_testing.md").write_text(
            testing_content, encoding="utf-8"
        )

        # fix_scope.md
        fix_scope_content = self._render_fix_scope_guide()
        (self._guides_dir / "fix_scope.md").write_text(
            fix_scope_content, encoding="utf-8"
        )

        logger.info("Generated default guides in %s", self._guides_dir)

    # ─── Private helpers ───

    def _extract_category(self, filename: str) -> str:
        """Extract category from filename prefix.

        Examples:
            ``always_code_style`` → ``"always"``
            ``fix_scope`` → ``"fix"``
            ``develop_guidelines`` → ``"develop"``
            ``unknown_name`` → ``"always"`` (fallback)
        """
        known_prefixes = ["always", "fix", "develop", "verify", "evaluate"]
        for prefix in known_prefixes:
            if filename.startswith(prefix + "_") or filename == prefix:
                return prefix
        return "always"  # fallback: treat as always-on

    def _normalize_category(self, category: str) -> str:
        """Normalize an operation category to its guide prefix.

        Examples:
            ``fix_issue`` → ``"fix"``
            ``develop_feature`` → ``"develop"``
            ``verify_feature`` → ``"verify"``
            ``evaluate_feature`` → ``"evaluate"``
        """
        mapping = {
            "fix_issue": "fix",
            "develop_feature": "develop",
            "verify_feature": "verify",
            "evaluate_feature": "evaluate",
        }
        if category in mapping:
            return mapping[category]
        return category.split("_")[0] if "_" in category else category

    def _render_code_style_guide(self, conventions: str) -> str:
        """Render the default code-style guide template."""
        base = (
            "# Code Style Guide\n\n"
            "Follow the project's existing code style:\n"
            "- Do not introduce new dependencies without justification\n"
            "- Keep changes minimal and focused\n"
            "- Follow existing naming conventions\n"
        )
        if conventions:
            base += f"\n## Project Conventions\n\n{conventions}\n"
        return base

    def _render_testing_guide(self, test_framework: str) -> str:
        """Render the default testing-requirements guide template."""
        content = (
            "# Testing Requirements\n\n"
            "When modifying code:\n"
            "- Add tests for new functionality\n"
            "- Update existing tests if behavior changes\n"
            "- Do not remove tests without justification\n"
            "- Ensure all existing tests still pass\n"
        )
        if test_framework:
            content += f"\nTest framework: {test_framework}\n"
        return content

    def _render_fix_scope_guide(self) -> str:
        """Render the default fix-scope guide template."""
        return (
            "# Fix Scope Constraints\n\n"
            "When fixing an issue:\n"
            "- Only fix the reported problem\n"
            "- Do not refactor unrelated code\n"
            "- Do not change API signatures unless necessary for the fix\n"
            "- Add/update tests for your fix\n"
            "- Keep the diff as small as possible\n"
            "- Do not change formatting of untouched lines\n"
        )


__all__ = ["Guide", "GuidesManager"]
