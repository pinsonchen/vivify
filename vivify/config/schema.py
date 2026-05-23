"""Pydantic schema for ``.vivify.yml``.

Mirrors the configuration shape declared in plan §4. Every field has a
sensible default so a freshly-initialised repo can run with an empty config.
"""
from __future__ import annotations

from typing import List, Literal

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
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    kpi_monitor: KpiMonitorConfig = Field(default_factory=KpiMonitorConfig)
    self_growth: SelfGrowthConfig = Field(default_factory=SelfGrowthConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    deploy: "DeployConfig" = Field(default_factory=DeployConfig)
    project: ProjectConfig = Field(default_factory=ProjectConfig)


__all__ = [
    "AgentConfig",
    "DeployConfig",
    "VivifyConfig",
    "DaemonConfig",
    "EscalationConfig",
    "FixersConfig",
    "GitHubConfig",
    "GoalsConfig",
    "KpiMonitorConfig",
    "PrConfig",
    "ProbesConfig",
    "ProjectConfig",
    "QoderCliConfig",
    "RemoteStorageConfig",
    "SelfGrowthConfig",
    "SqliteConfig",
    "StorageConfig",
]
