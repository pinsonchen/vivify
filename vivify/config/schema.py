"""Pydantic schema for ``.vivify.yml``.

Mirrors the configuration shape declared in plan §4. Every field has a
sensible default so a freshly-initialised repo can run with an empty config.
"""
from __future__ import annotations

from typing import Dict, List, Literal

from pydantic import BaseModel, Field, ConfigDict


# ────────────────────────────────────────────────────────────────────────────────
# Sub-models
# ────────────────────────────────────────────────────────────────────────────────


class PrConfig(BaseModel):
    base_branch: str = "main"
    branch_prefix: str = "vivify/"
    auto_merge: bool = False
    labels: List[str] = Field(default_factory=lambda: ["vivify"])
    draft_default: bool = False
    fetch_timeout_seconds: int = 120
    merge_poll_timeout_seconds: int = 120  # 等待 PR 合并的超时（秒），0=不等待


class QoderCliConfig(BaseModel):
    binary_path: str = "qodercli"
    model: str = "ultimate"
    max_turns_fix: int = 30
    max_turns_develop: int = 100
    max_turns_evaluate: int = 20
    max_turns_verify: int = 20
    max_turns_decompose: int = 30
    timeout_fix_seconds: int = 1800
    timeout_develop_seconds: int = 3600
    timeout_evaluate_seconds: int = 600
    timeout_verify_seconds: int = 600
    timeout_decompose_seconds: int = 600
    extra_args: List[str] = Field(default_factory=lambda: ["--yolo", "-q"])
    max_concurrent_processes: int = 10
    slot_wait_timeout_seconds: int = 300
    auto_trust_workspace: bool = True
    permission_mode: str = "bypass_permissions"  # default/accept_edits/bypass_permissions/dont_ask/plan/auto
    # Remote (cloud) execution
    use_remote: bool = False
    remote_poll_interval: int = 15
    remote_timeout: int = 900
    max_concurrent_remote: int = 3
    plan_agent_for_decompose: bool = True
    # 推理努力级别（按任务类别）
    reasoning_effort_by_category: Dict[str, str] = Field(
        default_factory=lambda: {
            "fix_issue": "high",
            "develop_feature": "medium",
            "evaluate_feature": "medium",
            "verify_feature": "high",
            "goal_decompose": "medium",
        }
    )
    # 系统提示追加内容（非空时追加到 qodercli 的 system prompt）
    system_prompt_suffix: str = ""
    # 最大输出 token（按任务类别）
    max_output_tokens_by_category: Dict[str, int] = Field(
        default_factory=lambda: {
            "fix_issue": 16000,
            "develop_feature": 32000,
            "evaluate_feature": 8000,
            "verify_feature": 8000,
            "goal_decompose": 8000,
        }
    )
    # Agent 选择（按任务类别，未配置的类别不传 --agent）
    agent_for_category: Dict[str, str] = Field(
        default_factory=lambda: {
            "goal_decompose": "Plan",
        }
    )
    # 附件最大数量（--attachment 参数）
    max_attachments: int = 3


class AgentConfig(BaseModel):
    type: Literal["qodercli"] = "qodercli"
    qodercli: QoderCliConfig = Field(default_factory=QoderCliConfig)


class SqliteConfig(BaseModel):
    path: str = ".vivify/state.db"


class RemoteStorageConfig(BaseModel):
    base_url: str = ""
    secret_env: str = "VIVIFY_SECRET"
    timeout_seconds: int = 10


class StorageConfig(BaseModel):
    type: Literal["sqlite", "remote"] = "sqlite"
    sqlite: SqliteConfig = Field(default_factory=SqliteConfig)
    remote: RemoteStorageConfig = Field(default_factory=RemoteStorageConfig)


class GitHubConfig(BaseModel):
    enabled: bool = True
    repo: str = ""             # auto-detected from ``git remote`` when blank
    token_env: str = "GH_TOKEN"  # 环境变量名（向后兼容）
    token: str = ""               # 实例级 token（直接存储，优先级最高）
    mirror_issues: bool = True


class ProbesConfig(BaseModel):
    enabled: List[str] = Field(default_factory=lambda: [
        "ci_status",
        "dependency_vulnerabilities",
        "test_coverage",
        "error_log_patterns",
        "lint_typecheck",
        "github_issue_backlog",
        "build_duration",
        "repo_size",
        "doc_staleness",
        "dead_code",
        "stale_branches",
        "secrets_scan",
    ])
    user_probes_dir: str = ".vivify/probes"
    per_probe_timeout_seconds: int = 120
    overrides: dict = Field(default_factory=dict)


class FixersConfig(BaseModel):
    enabled: List[str] = Field(default_factory=lambda: [
        "dependency_bump",
        "lint_autofix",
        "format_autofix",
        "test_flake_retry",
        "stale_branch_prune",
        "doc_link_check",
    ])
    user_fixers_dir: str = ".vivify/fixers"


class GoalsConfig(BaseModel):
    path: str = "GOALS.md"
    decompose_interval_hours: int = 24
    decompose_on_change: bool = True
    max_features_per_decompose: int = 3


class AgentCostModel(BaseModel):
    """按优先级分配 Agent 资源 (max_turns / timeout)."""

    p0_max_turns: int = 100
    p0_timeout: int = 7200    # 2 小时
    p1_max_turns: int = 60
    p1_timeout: int = 3600    # 1 小时
    p2_max_turns: int = 30
    p2_timeout: int = 1800    # 30 分钟
    p3_max_turns: int = 15
    p3_timeout: int = 900     # 15 分钟


class FeaturePipelineConfig(BaseModel):
    """Lifecycle thresholds for the feature pipeline.

    Used by the kernel's per-round timeout-recovery sweep to reset features
    that have been stuck in transient states (``evaluating`` / ``developing``
    / ``verifying``) longer than the configured threshold. Features whose
    ``retry_count`` reaches ``max_retries`` are auto-rejected.
    """

    evaluating_timeout_minutes: int = 10
    developing_timeout_minutes: int = 90
    verifying_timeout_minutes: int = 60
    max_retries: int = 3
    max_verify_retries: int = 2        # 验证失败最大重试次数
    auto_revert_enabled: bool = True   # 是否启用自动 revert
    cost_model: AgentCostModel = Field(default_factory=AgentCostModel)


class EscalationConfig(BaseModel):
    max_same_issue_rounds: int = 3
    upgrade_threshold: int = 3
    low_cooldown_seconds: int = 21600
    medium_cooldown_seconds: int = 3600


class KpiMonitorConfig(BaseModel):
    enabled: bool = True
    check_interval_hours: int = 24
    degrade_ratio: float = 0.8
    baseline_window_days: int = 30
    metrics: List[dict] = Field(default_factory=list)  # KPI-shaped dicts


class SelfGrowthConfig(BaseModel):
    enabled: bool = False
    allowed_paths: List[str] = Field(default_factory=lambda: [
        "vivify/probes/builtin/",
        "vivify/fixers/builtin/",
        "vivify/agents/prompts/templates/",
        "vivify/agents/prompts/snippets.py",
    ])
    kernel_modification: Literal["pr_with_two_approvals", "never_allowed"] = (
        "pr_with_two_approvals"
    )
    test_command: str = "pytest -x tests/unit"


class DaemonConfig(BaseModel):
    """Daemon process management settings."""

    pid_file: str = "vivify.pid"           # 相对于 state_dir
    lock_file: str = "vivify.lock"         # 相对于 state_dir
    stop_grace_seconds: int = 30
    log_stdout: bool = False               # daemon 模式是否将 stdout 写入日志


class DeployConfig(BaseModel):
    """部署配置"""

    enabled: bool = True                      # 是否启用自动部署
    # manual | ssh | rsync | command | webhook | github-pages | vercel | netlify
    method: str = "manual"
    # SSH/rsync 配置
    ssh_host: str = ""
    ssh_user: str = ""
    ssh_path: str = ""
    ssh_key: str = ""                         # SSH 私钥路径
    ssh_mode: str = "rsync"                   # rsync | git_pull
    source_dir: str = ""                      # 同步源子目录（相对于项目根），为空则同步整个项目
    # 自定义命令
    deploy_command: str = ""
    deploy_timeout_seconds: int = 300
    # Webhook
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_timeout_seconds: int = 30
    # 通用
    post_deploy_wait_seconds: int = 30        # 部署后等待时间（秒）
    verify_after_deploy: bool = True          # 是否部署后验证


class IntelligenceConfig(BaseModel):
    """智能分析配置 (RCA 与趋势分析)."""

    rca_enabled: bool = True
    rca_recurrence_threshold: int = 3      # 重复 N 次触发 RCA
    rca_max_per_round: int = 2             # 每轮最多 2 个 RCA 分析
    trend_enabled: bool = True
    trend_interval_rounds: int = 10        # 每 10 轮执行一次趋势分析
    trend_window_days: int = 7             # 分析窗口 7 天
    knowledge_history_injection: bool = True  # 注入历史经验到修复 prompt


class BudgetLimitConfig(BaseModel):
    """Token budget — API call rate limiting and p53 tumor suppression."""

    daily_limit: int = 100               # 每日 API 调用硬限
    per_cycle_limit: int = 10            # 每轮循环最大调用数
    window_seconds: int = 86400          # 时间窗口（默认 24h）
    pr_frequency_threshold: int = 10     # 24h 内 PR 数超过此值 → p53 降频
    backlog_threshold: int = 20          # 待处理 FR 积压超过此值 → p53 降频
    cooldown_multiplier: float = 2.0     # 降频时循环间隔倍增系数


class CapsuleConfig(BaseModel):
    """Skill capsule (fix-experience reuse) configuration."""

    enabled: bool = True
    capsules_dir: str = ".vivify/capsules"
    promote_threshold: int = 3      # 成功 N 次后建议提升
    archive_threshold: int = 5      # 成功 N 次后归档
    min_effectiveness: float = 0.7  # 最低有效率


class KnowledgeGCConfig(BaseModel):
    """Knowledge graph garbage collection configuration."""

    enabled: bool = True
    max_nodes: int = 500               # 图谱节点硬限
    max_modules: int = 50              # 模块卡片硬限
    stale_days: int = 30               # N 天未被引用视为 stale
    archive_after_days: int = 60       # N 天后归档
    delete_after_days: int = 90        # N 天后删除
    min_access_count: int = 2          # 最低引用次数（低于此值加速老化）
    gc_interval_hours: int = 24        # GC 执行间隔


class EpigeneticsConfig(BaseModel):
    """Epigenetics layer — probe expression regulation based on environment."""

    enabled: bool = True
    plasticity_window: int = 50        # 前 N 轮为高可塑期
    imprint_threshold: int = 3         # 可塑期内命中 N 次形成印记
    min_miss_streak: int = 10          # 连续无命中 N 轮才开始下调


class VerificationConfig(BaseModel):
    """Data-driven verification thresholds (Task #119)."""

    data_driven_enabled: bool = True
    min_quality_delta: float = -0.1
    allow_test_regression: bool = False
    allow_lint_regression: bool = True
    confidence_threshold: float = 0.7
    collect_baseline: bool = True    # 开发前是否自动采集 baseline


class HarnessConfig(BaseModel):
    """Project harness configuration for PEV loop."""

    enabled: bool = True
    # Verification sensors
    test_command: str = ""
    lint_command: str = ""
    typecheck_command: str = ""
    build_command: str = ""
    # Feedback control
    run_tests_after_fix: bool = True
    run_lint_after_fix: bool = True
    max_feedback_retries: int = 2
    feedback_timeout_seconds: int = 120
    # Feedforward guides
    guides_dir: str = ".vivify/guides"
    inject_guides_to_prompt: bool = True
    # Doom-loop detection
    doom_loop_window: int = 10
    doom_loop_threshold: int = 3
    # Risk scoring
    risk_scoring_enabled: bool = True
    high_risk_requires_tests: bool = True


class ProjectConfig(BaseModel):
    """项目元数据配置，由 vivify init 智能分析自动填充。"""

    name: str = ""
    description: str = ""
    type: str = "generic"          # ScenarioType 值: static-site, web-app, api-service, python-package, cli-tool, docs-only, mobile-app, monorepo, infra, generic
    language: str = ""             # 主要编程语言
    framework: str = ""            # 主框架 (react, vue, django, flask, etc.)
    deploy_url: str = ""           # 部署地址 (如 https://example.com)
    deploy_method: str = "manual"  # manual | github-pages | vercel | netlify | ssh
    health_endpoint: str = ""      # 健康检查端点 (API 服务用, 如 /health)
    test_command: str = ""         # 测试命令 (如 pytest, npm test)
    build_command: str = ""        # 构建命令 (如 npm run build)
    dev_command: str = ""          # 开发服务命令 (如 npm run dev)
    wiki_path: str = ""            # qodercli wiki 输出目录，供 feature pipeline 引用项目架构上下文


# ────────────────────────────────────────────────────────────────────────────────
# Top-level
# ────────────────────────────────────────────────────────────────────────────────


class VivifyConfig(BaseModel):
    """Top-level ``.vivify.yml`` schema."""
    model_config = ConfigDict(extra="allow")

    version: int = 1
    mode: Literal["daemon", "once", "dry-run"] = "daemon"
    interval_seconds: int = 300
    state_dir: str = ".vivify"
    log_dir: str = ".vivify/logs"
    write_mode: Literal["pr"] = "pr"
    pr: PrConfig = Field(default_factory=PrConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    probes: ProbesConfig = Field(default_factory=ProbesConfig)
    fixers: FixersConfig = Field(default_factory=FixersConfig)
    goals: GoalsConfig = Field(default_factory=GoalsConfig)
    feature_pipeline: FeaturePipelineConfig = Field(default_factory=FeaturePipelineConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    kpi_monitor: KpiMonitorConfig = Field(default_factory=KpiMonitorConfig)
    self_growth: SelfGrowthConfig = Field(default_factory=SelfGrowthConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    deploy: "DeployConfig" = Field(default_factory=DeployConfig)
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    intelligence: IntelligenceConfig = Field(default_factory=IntelligenceConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    budget: BudgetLimitConfig = Field(default_factory=BudgetLimitConfig)
    capsules: CapsuleConfig = Field(default_factory=CapsuleConfig)
    knowledge_gc: KnowledgeGCConfig = Field(default_factory=KnowledgeGCConfig)
    epigenetics: EpigeneticsConfig = Field(default_factory=EpigeneticsConfig)
    rules: List[dict] = Field(default_factory=list)  # 复合信号规则配置


__all__ = [
    "AgentConfig",
    "AgentCostModel",
    "BudgetLimitConfig",
    "CapsuleConfig",
    "EpigeneticsConfig",
    "DeployConfig",
    "VivifyConfig",
    "DaemonConfig",
    "EscalationConfig",
    "FeaturePipelineConfig",
    "FixersConfig",
    "GitHubConfig",
    "GoalsConfig",
    "HarnessConfig",
    "IntelligenceConfig",
    "KnowledgeGCConfig",
    "KpiMonitorConfig",
    "PrConfig",
    "ProbesConfig",
    "ProjectConfig",
    "QoderCliConfig",
    "RemoteStorageConfig",
    "SelfGrowthConfig",
    "SqliteConfig",
    "StorageConfig",
    "VerificationConfig",
]
