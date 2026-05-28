"""Tests for ``vivify.config.loader`` advanced merging."""

from __future__ import annotations

from pathlib import Path

import pytest

from vivify.config.loader import _deep_merge, load_config


# ============================================================
# _deep_merge
# ============================================================


class TestDeepMerge:
    """``_deep_merge`` 行为单测。"""

    def test_deep_merge_simple(self) -> None:
        """浅层覆盖：override 中的值替换 base 中同名标量。"""
        base = {"a": 1, "b": 2}
        override = {"b": 20, "c": 3}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 20, "c": 3}

    def test_deep_merge_nested(self) -> None:
        """嵌套 dict 应递归合并而非整段替换。"""
        base = {"agent": {"qodercli": {"model": "ultimate", "max_turns_fix": 30}}}
        override = {"agent": {"qodercli": {"max_turns_fix": 50}}}
        result = _deep_merge(base, override)
        assert result == {
            "agent": {
                "qodercli": {"model": "ultimate", "max_turns_fix": 50},
            }
        }

    def test_deep_merge_new_keys(self) -> None:
        """override 中独有的新键应被追加。"""
        base = {"agent": {"qodercli": {"model": "ultimate"}}}
        override = {
            "agent": {"qodercli": {"max_turns_develop": 200}},
            "intelligence": {"rca_enabled": True},
        }
        result = _deep_merge(base, override)
        assert result["agent"]["qodercli"] == {
            "model": "ultimate",
            "max_turns_develop": 200,
        }
        assert result["intelligence"] == {"rca_enabled": True}

    def test_deep_merge_override_wins(self) -> None:
        """同名标量冲突时 override 优先。"""
        base = {"x": "old", "nested": {"k": 1}}
        override = {"x": "new", "nested": {"k": 2}}
        result = _deep_merge(base, override)
        assert result["x"] == "new"
        assert result["nested"]["k"] == 2

    def test_deep_merge_does_not_mutate_inputs(self) -> None:
        """合并后原始 base/override 不应被修改。"""
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        original_base = {"a": {"b": 1}}
        original_override = {"a": {"c": 2}}
        _ = _deep_merge(base, override)
        assert base == original_base
        assert override == original_override


# ============================================================
# load_config + advanced
# ============================================================


def _write_main(path: Path, content: str) -> Path:
    cfg = path / ".vivify.yml"
    cfg.write_text(content, encoding="utf-8")
    return cfg


class TestLoadConfigAdvanced:
    """主配置 + ``.vivify-advanced.yml`` 合并行为。"""

    def test_load_config_with_advanced(self, tmp_path: Path) -> None:
        """advanced 文件应覆盖主配置同名字段。"""
        cfg_path = _write_main(
            tmp_path,
            "version: 1\n"
            "project:\n"
            '  name: "demo"\n'
            "agent:\n"
            "  qodercli:\n"
            "    max_turns_fix: 30\n",
        )
        (tmp_path / ".vivify-advanced.yml").write_text(
            "agent:\n"
            "  qodercli:\n"
            "    max_turns_fix: 99\n"
            "    max_turns_develop: 200\n",
            encoding="utf-8",
        )
        cfg = load_config(cfg_path)
        assert cfg.project.name == "demo"
        # advanced 覆盖
        assert cfg.agent.qodercli.max_turns_fix == 99
        # advanced 新增
        assert cfg.agent.qodercli.max_turns_develop == 200

    def test_load_config_without_advanced(self, tmp_path: Path) -> None:
        """无 advanced 文件时主配置正常加载。"""
        cfg_path = _write_main(
            tmp_path,
            "version: 1\n"
            "project:\n"
            '  name: "demo"\n'
            "agent:\n"
            "  qodercli:\n"
            "    max_turns_fix: 42\n",
        )
        cfg = load_config(cfg_path)
        assert cfg.project.name == "demo"
        assert cfg.agent.qodercli.max_turns_fix == 42

    def test_load_config_advanced_error_ignored(self, tmp_path: Path) -> None:
        """advanced 文件 YAML 错误不应阻塞主配置加载。"""
        cfg_path = _write_main(
            tmp_path,
            "version: 1\nproject:\n  name: \"demo\"\n",
        )
        # 写入非法 YAML
        (tmp_path / ".vivify-advanced.yml").write_text(
            "agent:\n  qodercli:\n    : invalid : :\n   bad indent",
            encoding="utf-8",
        )
        cfg = load_config(cfg_path)  # 不应抛异常
        assert cfg.project.name == "demo"

    def test_load_config_advanced_empty_file(self, tmp_path: Path) -> None:
        """空 advanced 文件不应影响加载。"""
        cfg_path = _write_main(
            tmp_path,
            "version: 1\nproject:\n  name: \"x\"\n",
        )
        (tmp_path / ".vivify-advanced.yml").write_text("", encoding="utf-8")
        cfg = load_config(cfg_path)
        assert cfg.project.name == "x"

    def test_load_config_missing_main(self, tmp_path: Path) -> None:
        """主配置不存在时返回默认值，但仍尝试合并 advanced（如存在）。"""
        # 仅创建 advanced 文件
        (tmp_path / ".vivify-advanced.yml").write_text(
            "agent:\n  qodercli:\n    max_turns_fix: 7\n",
            encoding="utf-8",
        )
        non_existent = tmp_path / ".vivify.yml"
        cfg = load_config(non_existent)
        # advanced 已被合并
        assert cfg.agent.qodercli.max_turns_fix == 7


@pytest.mark.parametrize(
    "base,override,expected",
    [
        ({}, {"a": 1}, {"a": 1}),
        ({"a": 1}, {}, {"a": 1}),
        ({"a": [1, 2]}, {"a": [3]}, {"a": [3]}),  # list 是非 dict，直接覆盖
    ],
)
def test_deep_merge_edge_cases(base, override, expected) -> None:
    """边界场景：空 dict、list 直接覆盖。"""
    assert _deep_merge(base, override) == expected
