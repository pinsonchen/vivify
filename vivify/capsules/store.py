"""Capsule storage — manages ``.vivify/capsules/*.json`` files."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from vivify.capsules.models import SkillCapsule

logger = logging.getLogger(__name__)


_KEYWORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on word boundaries; drop very short tokens."""
    if not text:
        return set()
    return {m.group(0).lower() for m in _KEYWORD_RE.finditer(text)}


class CapsuleStore:
    """Persist and look up :class:`SkillCapsule` instances on disk."""

    def __init__(self, capsules_dir: Path):
        self.capsules_dir = Path(capsules_dir)
        self.capsules_dir.mkdir(parents=True, exist_ok=True)

    # ── file IO ──────────────────────────────────────────────────────────
    def _path_for(self, capsule_id: str) -> Path:
        # Sanitise id so weird characters can't escape the directory.
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", capsule_id)
        return self.capsules_dir / f"{safe}.json"

    def save(self, capsule: SkillCapsule) -> None:
        """Save/update a capsule to disk."""
        path = self._path_for(capsule.capsule_id)
        try:
            path.write_text(
                json.dumps(capsule.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:  # pragma: no cover
            logger.warning("Failed to save capsule %s: %s", capsule.capsule_id, e)

    def load(self, capsule_id: str) -> Optional[SkillCapsule]:
        """Load a single capsule by id; return ``None`` if missing/corrupt."""
        path = self._path_for(capsule_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SkillCapsule.from_dict(data)
        except (OSError, ValueError, KeyError) as e:
            logger.warning("Skipping corrupt capsule %s: %s", path.name, e)
            return None

    def load_all(self, *, include_archived: bool = False) -> List[SkillCapsule]:
        """Load all active capsules (skips archived by default)."""
        results: List[SkillCapsule] = []
        if not self.capsules_dir.exists():
            return results
        for path in sorted(self.capsules_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                cap = SkillCapsule.from_dict(data)
            except (OSError, ValueError, KeyError) as e:
                logger.warning("Skipping corrupt capsule %s: %s", path.name, e)
                continue
            if not include_archived and cap.status == "archived":
                continue
            results.append(cap)
        return results

    def delete(self, capsule_id: str) -> bool:
        """Remove a capsule; return ``True`` if a file was deleted."""
        path = self._path_for(capsule_id)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as e:  # pragma: no cover
            logger.warning("Failed to delete capsule %s: %s", capsule_id, e)
            return False

    # ── matching ────────────────────────────────────────────────────────
    def find_matching(
        self, probe_id: str, issue_text: str
    ) -> Optional[SkillCapsule]:
        """Find a matching capsule for the given issue.

        Matching logic:
        1. Exact ``probe_id`` match
        2. ``issue_text`` shares at least one keyword with the trigger pattern
        3. Among ties, return the one with highest ``effectiveness``; break
           further ties by larger ``success_count``.
        """
        if not probe_id and not issue_text:
            return None
        candidates = [
            c
            for c in self.load_all()
            if c.status == "active" and c.probe_id == probe_id
        ]
        if not candidates:
            return None

        issue_tokens = _tokenize(issue_text)
        scored: list[tuple[float, int, SkillCapsule]] = []
        for cap in candidates:
            pattern_tokens = _tokenize(cap.trigger_pattern)
            # If the capsule has no informative pattern tokens, treat as
            # generic match (probe_id alone is enough).
            if pattern_tokens and issue_tokens:
                if not (pattern_tokens & issue_tokens):
                    continue
            scored.append((cap.effectiveness, cap.success_count, cap))

        if not scored:
            return None
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return scored[0][2]

    # ── usage tracking ──────────────────────────────────────────────────
    def record_usage(self, capsule_id: str, success: bool) -> None:
        """Record a capsule reuse outcome."""
        cap = self.load(capsule_id)
        if cap is None:
            return
        if success:
            cap.success_count += 1
        else:
            cap.failure_count += 1
        cap.last_used = datetime.now()
        # Auto-archive when criteria met; promotion is left to operator action.
        if cap.should_archive and cap.status == "active":
            cap.status = "archived"
        self.save(cap)

    def get_promotion_candidates(self) -> List[SkillCapsule]:
        """Get capsules ready for promotion."""
        return [c for c in self.load_all() if c.should_promote and c.status == "active"]


__all__ = ["CapsuleStore"]
