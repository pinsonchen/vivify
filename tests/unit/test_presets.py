"""Tests for ``vivify.config.presets`` scenario presets."""

from __future__ import annotations

from vivify.config.presets import (
    QODERCLI_PRESETS,
    get_preset,
    get_preset_value,
)
from vivify.intelligence.classifier import ScenarioType


REQUIRED_KEYS = {
    "max_turns_fix",
    "max_turns_develop",
    "max_turns_evaluate",
    "max_turns_verify",
    "max_turns_decompose",
    "timeout_fix_seconds",
    "timeout_develop_seconds",
    "timeout_evaluate_seconds",
    "timeout_verify_seconds",
    "timeout_decompose_seconds",
}


class TestGetPreset:
    """``get_preset`` 行为测试。"""

    def test_get_preset_web_app(self) -> None:
        """web-app 预设应包含全部必需字段。"""
        preset = get_preset("web-app")
        assert REQUIRED_KEYS.issubset(preset.keys())
        assert preset["max_turns_fix"] == 30
        assert preset["max_turns_develop"] == 100
        assert preset["timeout_fix_seconds"] == 1800

    def test_get_preset_api_service(self) -> None:
        """api-service 应有更高 turns 与 timeout（相比 web-app）。"""
        api = get_preset("api-service")
        web = get_preset("web-app")
        assert api["max_turns_fix"] > web["max_turns_fix"]
        assert api["max_turns_develop"] > web["max_turns_develop"]
        assert api["timeout_fix_seconds"] > web["timeout_fix_seconds"]

    def test_get_preset_docs_only(self) -> None:
        """docs-only 应有更低的 turns/timeout。"""
        docs = get_preset("docs-only")
        web = get_preset("web-app")
        assert docs["max_turns_fix"] < web["max_turns_fix"]
        assert docs["max_turns_develop"] < web["max_turns_develop"]
        assert docs["timeout_fix_seconds"] < web["timeout_fix_seconds"]

    def test_get_preset_unknown_fallback(self) -> None:
        """未知场景应回退到 generic 预设。"""
        unknown = get_preset("not-a-scenario")
        generic = get_preset("generic")
        assert unknown == generic

    def test_get_preset_returns_dict(self) -> None:
        """get_preset 返回类型应为 dict。"""
        assert isinstance(get_preset("web-app"), dict)


class TestGetPresetValue:
    """``get_preset_value`` 单字段读取测试。"""

    def test_get_preset_value(self) -> None:
        """已知字段返回对应值。"""
        assert get_preset_value("web-app", "max_turns_fix") == 30
        assert (
            get_preset_value("api-service", "timeout_fix_seconds") == 2400
        )

    def test_get_preset_value_default(self) -> None:
        """字段缺失时返回 default。"""
        assert (
            get_preset_value("web-app", "non_existent_key", default="x") == "x"
        )
        assert get_preset_value("web-app", "non_existent_key") is None

    def test_get_preset_value_unknown_scenario(self) -> None:
        """未知场景下回退 generic 后查 key。"""
        assert get_preset_value("nope", "max_turns_fix") == 30


class TestPresetCoverage:
    """预设完整性覆盖检查。"""

    def test_all_scenarios_covered(self) -> None:
        """所有 ScenarioType 枚举值都应有预设。"""
        for s in ScenarioType:
            assert s.value in QODERCLI_PRESETS, f"missing preset: {s.value}"

    def test_all_presets_have_required_keys(self) -> None:
        """每个预设都需含 10 个必需字段。"""
        for name, preset in QODERCLI_PRESETS.items():
            missing = REQUIRED_KEYS - preset.keys()
            assert not missing, f"{name} missing keys: {missing}"

    def test_all_preset_values_positive(self) -> None:
        """每个预设的 turns/timeout 都应为正整数。"""
        for name, preset in QODERCLI_PRESETS.items():
            for key in REQUIRED_KEYS:
                value = preset[key]
                assert isinstance(value, int) and value > 0, (
                    f"{name}.{key}={value!r} should be positive int"
                )
