"""Config package — schema + loader + defaults."""
from vivify.config.defaults import (
    DEFAULT_BUILTIN_FIXERS,
    DEFAULT_BUILTIN_PROBES,
    DEFAULT_GITIGNORE_ENTRIES,
)
from vivify.config.loader import load_config
from vivify.config.schema import (
    AgentConfig,
    VivifyConfig,
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
    "VivifyConfig",
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
