"""Skill Capsule data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SkillCapsule:
    """A reusable fix strategy learned from successful repairs."""

    capsule_id: str                     # 唯一 ID (uuid4)
    trigger_pattern: str                # 触发条件 (probe_id + issue 特征描述)
    fix_strategy: str                   # 修复策略摘要（人可读）
    prompt_template: str                # 注入 agent prompt 的模板片段
    source_action_id: str               # 来源 ActionLog ID
    probe_id: str                       # 关联的探针 ID
    issue_category: str                 # 问题分类 (lint/test/build/security/etc)

    success_count: int = 0              # 被复用成功的次数
    failure_count: int = 0              # 被复用失败的次数
    created_at: datetime = field(default_factory=datetime.now)
    last_used: Optional[datetime] = None

    # 生命周期状态
    status: str = "active"              # active / promoted / archived
    promoted_to: Optional[str] = None   # 提升为什么（CI step / git hook / script）

    @property
    def effectiveness(self) -> float:
        """Success rate of this capsule."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total

    @property
    def should_promote(self) -> bool:
        """Whether capsule should be promoted to native project capability."""
        return self.success_count >= 3 and self.effectiveness >= 0.7

    @property
    def should_archive(self) -> bool:
        """Whether capsule should be archived (graduated from Vivify)."""
        return self.success_count >= 5 and self.effectiveness >= 0.8

    # ── serialization helpers ─────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "capsule_id": self.capsule_id,
            "trigger_pattern": self.trigger_pattern,
            "fix_strategy": self.fix_strategy,
            "prompt_template": self.prompt_template,
            "source_action_id": self.source_action_id,
            "probe_id": self.probe_id,
            "issue_category": self.issue_category,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "status": self.status,
            "promoted_to": self.promoted_to,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SkillCapsule":
        created_at = data.get("created_at")
        last_used = data.get("last_used")
        return cls(
            capsule_id=data["capsule_id"],
            trigger_pattern=data["trigger_pattern"],
            fix_strategy=data["fix_strategy"],
            prompt_template=data["prompt_template"],
            source_action_id=data.get("source_action_id", ""),
            probe_id=data.get("probe_id", ""),
            issue_category=data.get("issue_category", ""),
            success_count=int(data.get("success_count", 0)),
            failure_count=int(data.get("failure_count", 0)),
            created_at=(
                datetime.fromisoformat(created_at) if created_at else datetime.now()
            ),
            last_used=datetime.fromisoformat(last_used) if last_used else None,
            status=data.get("status", "active"),
            promoted_to=data.get("promoted_to"),
        )


__all__ = ["SkillCapsule"]
