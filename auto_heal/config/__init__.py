"""Config package — schema + loader + defaults."""
from auto_heal.config.defaults import (
    DEFAULT_BUILTIN_FIXERS,
    DEFAULT_BUILTIN_PROBES,
    DEFAULT_GITIGNORE_ENTRIES,
)
from auto_heal.config.loader import load_config
from auto_heal.config.schema import (
    AgentConfig,
    AutoHealConfig,
    EscalationConfig,
    FixersConfig,
    GitHubConfig,
    GoalsConfig,
    KpiMonitorConfig,
    PrConfig,
    ProbesConfig,
    QoderCliConfig,
    RemoteStorageConfig,
    SelfGrowthConfig,
    SqliteConfig,
    StorageConfig,
)

__all__ = [
    "AgentConfig",
    "AutoHealConfig",
    "DEFAULT_BUILTIN_FIXERS",
    "DEFAULT_BUILTIN_PROBES",
    "DEFAULT_GITIGNORE_ENTRIES",
    "EscalationConfig",
    "FixersConfig",
    "GitHubConfig",
    "GoalsConfig",
    "KpiMonitorConfig",
    "PrConfig",
    "ProbesConfig",
    "QoderCliConfig",
    "RemoteStorageConfig",
    "SelfGrowthConfig",
    "SqliteConfig",
    "StorageConfig",
    "load_config",
]
