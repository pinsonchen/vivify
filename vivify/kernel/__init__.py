"""Kernel — main orchestration loop and supporting state machines."""
from vivify.kernel.code_hash import compute_code_hash
from vivify.kernel.dispatch import (
    DispatchPolicy,
    DispatchState,
    mark_attempted,
    select_fixer,
    should_skip,
)
from vivify.kernel.escalator import EscalationPolicy, Escalator
from vivify.kernel.failure_tracker import FailureState, FailureTracker
from vivify.kernel.feature_pipeline import (
    FeaturePipeline,
    FeaturePipelineConfig,
    FeatureRunReport,
)
from vivify.kernel.health_monitor import (
    HealthMonitor,
    HealthMonitorConfig,
    Regression,
    detect_regressions,
)
from vivify.kernel.loop import Kernel, KernelConfig, KernelDeps, RoundReport
from vivify.kernel.safe_restart import safe_restart

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
