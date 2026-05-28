"""Scenario-based QoderCliConfig presets.

Each preset defines optimal parameters for a specific project type,
reducing the need for manual tuning.
"""
from __future__ import annotations


# Preset definitions keyed by ScenarioType value
QODERCLI_PRESETS: dict[str, dict] = {
    "web-app": {
        "max_turns_fix": 30,
        "max_turns_develop": 100,
        "max_turns_evaluate": 20,
        "max_turns_verify": 20,
        "max_turns_decompose": 30,
        "timeout_fix_seconds": 1800,
        "timeout_develop_seconds": 3600,
        "timeout_evaluate_seconds": 600,
        "timeout_verify_seconds": 600,
        "timeout_decompose_seconds": 600,
    },
    "api-service": {
        "max_turns_fix": 40,
        "max_turns_develop": 120,
        "max_turns_evaluate": 25,
        "max_turns_verify": 25,
        "max_turns_decompose": 35,
        "timeout_fix_seconds": 2400,
        "timeout_develop_seconds": 4200,
        "timeout_evaluate_seconds": 900,
        "timeout_verify_seconds": 900,
        "timeout_decompose_seconds": 900,
    },
    "python-package": {
        "max_turns_fix": 20,
        "max_turns_develop": 60,
        "max_turns_evaluate": 15,
        "max_turns_verify": 15,
        "max_turns_decompose": 20,
        "timeout_fix_seconds": 1200,
        "timeout_develop_seconds": 2400,
        "timeout_evaluate_seconds": 600,
        "timeout_verify_seconds": 600,
        "timeout_decompose_seconds": 600,
    },
    "cli-tool": {
        "max_turns_fix": 25,
        "max_turns_develop": 80,
        "max_turns_evaluate": 15,
        "max_turns_verify": 15,
        "max_turns_decompose": 25,
        "timeout_fix_seconds": 1500,
        "timeout_develop_seconds": 3000,
        "timeout_evaluate_seconds": 600,
        "timeout_verify_seconds": 600,
        "timeout_decompose_seconds": 600,
    },
    "docs-only": {
        "max_turns_fix": 10,
        "max_turns_develop": 30,
        "max_turns_evaluate": 10,
        "max_turns_verify": 10,
        "max_turns_decompose": 15,
        "timeout_fix_seconds": 600,
        "timeout_develop_seconds": 1200,
        "timeout_evaluate_seconds": 300,
        "timeout_verify_seconds": 300,
        "timeout_decompose_seconds": 300,
    },
    "static-site": {
        "max_turns_fix": 15,
        "max_turns_develop": 50,
        "max_turns_evaluate": 15,
        "max_turns_verify": 15,
        "max_turns_decompose": 20,
        "timeout_fix_seconds": 900,
        "timeout_develop_seconds": 1800,
        "timeout_evaluate_seconds": 600,
        "timeout_verify_seconds": 600,
        "timeout_decompose_seconds": 600,
    },
    "mobile-app": {
        "max_turns_fix": 30,
        "max_turns_develop": 100,
        "max_turns_evaluate": 20,
        "max_turns_verify": 20,
        "max_turns_decompose": 30,
        "timeout_fix_seconds": 1800,
        "timeout_develop_seconds": 3600,
        "timeout_evaluate_seconds": 600,
        "timeout_verify_seconds": 600,
        "timeout_decompose_seconds": 600,
    },
    "monorepo": {
        "max_turns_fix": 40,
        "max_turns_develop": 120,
        "max_turns_evaluate": 25,
        "max_turns_verify": 25,
        "max_turns_decompose": 40,
        "timeout_fix_seconds": 2400,
        "timeout_develop_seconds": 4800,
        "timeout_evaluate_seconds": 900,
        "timeout_verify_seconds": 900,
        "timeout_decompose_seconds": 900,
    },
    "infra": {
        "max_turns_fix": 15,
        "max_turns_develop": 40,
        "max_turns_evaluate": 10,
        "max_turns_verify": 10,
        "max_turns_decompose": 20,
        "timeout_fix_seconds": 900,
        "timeout_develop_seconds": 1800,
        "timeout_evaluate_seconds": 600,
        "timeout_verify_seconds": 600,
        "timeout_decompose_seconds": 600,
    },
    "generic": {
        "max_turns_fix": 30,
        "max_turns_develop": 100,
        "max_turns_evaluate": 20,
        "max_turns_verify": 20,
        "max_turns_decompose": 30,
        "timeout_fix_seconds": 1800,
        "timeout_develop_seconds": 3600,
        "timeout_evaluate_seconds": 600,
        "timeout_verify_seconds": 600,
        "timeout_decompose_seconds": 600,
    },
}

# Default fallback preset
_DEFAULT_PRESET = "generic"


def get_preset(scenario: str) -> dict:
    """Get QoderCliConfig preset for a given scenario type.

    Args:
        scenario: ScenarioType value (e.g. "web-app", "api-service").

    Returns:
        Dict of QoderCliConfig field overrides.
    """
    return QODERCLI_PRESETS.get(scenario, QODERCLI_PRESETS[_DEFAULT_PRESET])


def get_preset_value(scenario: str, key: str, default=None):
    """Get a specific value from a scenario preset.

    Args:
        scenario: ScenarioType value.
        key: QoderCliConfig field name.
        default: Fallback if key not in preset.
    """
    preset = get_preset(scenario)
    return preset.get(key, default)
