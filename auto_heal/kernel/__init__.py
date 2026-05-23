"""Kernel — main orchestration loop and supporting state machines."""
from auto_heal.kernel.code_hash import compute_code_hash
from auto_heal.kernel.dispatch import (
    DispatchPolicy,
    DispatchState,
    mark_attempted,
    select_fixer,
    should_skip,
)
from auto_heal.kernel.escalator import EscalationPolicy, Escalator
from auto_heal.kernel.failure_tracker import FailureState, FailureTracker
from auto_heal.kernel.feature_pipeline import (
    FeaturePipeline,
    FeaturePipelineConfig,
    FeatureRunReport,
)
from auto_heal.kernel.health_monitor import (
    HealthMonitor,
    HealthMonitorConfig,
    Regression,
    detect_regressions,
)
from auto_heal.kernel.loop import Kernel, KernelConfig, KernelDeps, RoundReport
from auto_heal.kernel.safe_restart import safe_restart

__all__ = [
    "compute_code_hash",
    "DispatchPolicy",
    "DispatchState",
    "EscalationPolicy",
    "Escalator",
    "FailureState",
    "FailureTracker",
    "FeaturePipeline",
    "FeaturePipelineConfig",
    "FeatureRunReport",
    "HealthMonitor",
    "HealthMonitorConfig",
    "Kernel",
    "KernelConfig",
    "KernelDeps",
    "Regression",
    "RoundReport",
    "detect_regressions",
    "mark_attempted",
    "safe_restart",
    "select_fixer",
    "should_skip",
]
