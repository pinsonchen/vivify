"""Title-similarity helper used to dedupe FeatureSpecs against open features."""
from __future__ import annotations

import difflib
import re
from typing import Iterable

_WORD_RE = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    return _WORD_RE.sub(" ", (text or "").lower()).strip()


def title_similarity(a: str, b: str) -> float:
    """Return ratio in [0, 1]; 1.0 means identical post-normalisation."""
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def is_duplicate(
    title: str, existing: Iterable[str], *, threshold: float = 0.85,
) -> bool:
    """Whether ``title`` matches any title in ``existing`` above ``threshold``."""
    for other in existing:
        if title_similarity(title, other) >= threshold:
            return True
    return False


__all__ = ["title_similarity", "is_duplicate"]
