"""Skill Capsule subsystem.

Skill capsules are reusable fix-strategy artefacts distilled from successful
agent repairs. The kernel saves them after a successful PR and looks them up
on the next matching issue, injecting the prompt template as fast-path
context for the agent.

Design goals:

* Pure rule-driven extraction — no LLM call, keep fast-path cheap.
* JSON-on-disk storage in ``.vivify/capsules/``; no new database table.
* Default-on but harmless when the directory is empty (full backwards-compat).
"""
from __future__ import annotations

from vivify.capsules.extractor import CapsuleExtractor
from vivify.capsules.externalizer import CapabilityExternalizer, ExternalizationPlan
from vivify.capsules.models import SkillCapsule
from vivify.capsules.store import CapsuleStore

__all__ = [
    "SkillCapsule",
    "CapsuleStore",
    "CapsuleExtractor",
    "CapabilityExternalizer",
    "ExternalizationPlan",
]
