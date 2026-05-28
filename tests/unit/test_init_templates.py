"""Tests for `vivify init` template builders (quick / full / advanced)."""

from __future__ import annotations

from dataclasses import dataclass

from vivify.cli.init_cmd import (
    _build_advanced_yaml,
    _build_quick_yaml,
    _build_yaml,
)
from vivify.intelligence.classifier import ScenarioType


@dataclass
class _FakeScenario:
    """Mimic ScenarioType enum interface (only `.value`)."""

    value: str


@dataclass
class _FakeProfile:
    """Lightweight stand-in for ProjectProfile used by the YAML builders."""

    primary_scenario: _FakeScenario
    language: str = "python"
    framework: str = ""


def _profile(scenario: str = "web-app", language: str = "python") -> _FakeProfile:
    return _FakeProfile(
        primary_scenario=_FakeScenario(value=scenario),
        language=language,
        framework="",
    )


# ============================================================
# _build_quick_yaml
# ============================================================


class TestQuickYaml:
    """快速启动模板生成器测试。"""

    def test_quick_yaml_basic(self) -> None:
        """quick 模板必含 version/mode/project/pr/agent 段。"""
        yaml = _build_quick_yaml(
            _profile("web-app"),
            {"project.name": "demo"},
            "main",
        )
        assert "version: 1" in yaml
        assert "mode: daemon" in yaml
        assert "project:" in yaml
        assert 'name: "demo"' in yaml
        assert 'type: "web-app"' in yaml
        assert "pr:" in yaml
        assert "base_branch: main" in yaml
        assert "agent:" in yaml
        assert "type: qodercli" in yaml

    def test_quick_yaml_with_harness(self) -> None:
        """检测到 harness 命令时应包含 harness 块。"""
        yaml = _build_quick_yaml(
            _profile(),
            {"project.name": "x"},
            "main",
            harness_commands={
                "test": "pytest",
                "lint": "ruff check .",
                "typecheck": "mypy .",
                "build": "make",
            },
        )
        assert "harness:" in yaml
        assert "enabled: true" in yaml
        assert 'test_command: "pytest"' in yaml
        assert 'lint_command: "ruff check ."' in yaml
        assert 'typecheck_command: "mypy ."' in yaml
        assert 'build_command: "make"' in yaml

    def test_quick_yaml_no_harness(self) -> None:
        """未检测到 harness 命令时不应输出 harness 块。"""
        yaml = _build_quick_yaml(
            _profile(),
            {"project.name": "x"},
            "main",
            harness_commands={},
        )
        assert "harness:" not in yaml

    def test_quick_yaml_with_github_token(self) -> None:
        """传入 token 时应生成 github 块。"""
        yaml = _build_quick_yaml(
            _profile(),
            {"project.name": "x"},
            "main",
            github_token="ghp_xxx",
        )
        assert "github:" in yaml
        assert 'token: "ghp_xxx"' in yaml
        assert "enabled: true" in yaml

    def test_quick_yaml_no_github_token(self) -> None:
        """未传 token 时不应包含 github 块。"""
        yaml = _build_quick_yaml(
            _profile(),
            {"project.name": "x"},
            "main",
            github_token="",
        )
        assert "github:" not in yaml

    def test_quick_yaml_line_count(self) -> None:
        """精简模板的行数应控制在 40 行以内。"""
        yaml = _build_quick_yaml(
            _profile(),
            {"project.name": "x"},
            "main",
            harness_commands={"test": "pytest"},
            github_token="ghp_xxx",
            wiki_path=".qoder/repowiki/zh",
        )
        assert yaml.count("\n") <= 40

    def test_quick_yaml_with_wiki_path(self) -> None:
        """传入 wiki_path 时应包含 wiki_path 字段。"""
        yaml = _build_quick_yaml(
            _profile(),
            {"project.name": "x"},
            "main",
            wiki_path=".qoder/repowiki/zh",
        )
        assert 'wiki_path: ".qoder/repowiki/zh"' in yaml


# ============================================================
# _build_yaml (full template)
# ============================================================


class TestFullYaml:
    """完整模板注释增强测试。"""

    def test_full_yaml_has_section_comments(self) -> None:
        """full 模板应包含中英文章节注释标识。"""
        yaml = _build_yaml(
            _profile("web-app"),
            {"project.name": "demo"},
            ["ci_status"],
            ["lint_autofix"],
            "main",
        )
        # 中文注释
        assert "项目基础信息" in yaml
        assert "PR 创建策略" in yaml
        assert "检测探针" in yaml
        assert "修复器" in yaml
        # 英文注释标识
        assert "Project Info" in yaml
        assert "Pull Request" in yaml
        assert "Probes" in yaml
        assert "Fixers" in yaml

    def test_full_yaml_has_advanced_markers(self) -> None:
        """高级字段应附带 # [高级] 标记。"""
        yaml = _build_yaml(
            _profile(),
            {"project.name": "x"},
            ["ci_status"],
            ["lint_autofix"],
            "main",
        )
        assert "# [高级]" in yaml
        # 关键高级字段
        assert "max_turns_fix: 30  # [高级]" in yaml
        assert "max_turns_develop: 100  # [高级]" in yaml
        assert "timeout_fix_seconds: 1800  # [高级]" in yaml

    def test_full_yaml_includes_github_token(self) -> None:
        """传入 github_token 时配置中应写入实例级 token。"""
        yaml = _build_yaml(
            _profile(),
            {"project.name": "x"},
            ["ci_status"],
            ["lint_autofix"],
            "main",
            github_token="ghp_abc",
        )
        assert 'token: "ghp_abc"' in yaml

    def test_full_yaml_harness_commands(self) -> None:
        """harness_commands 应正确写入对应字段。"""
        yaml = _build_yaml(
            _profile(),
            {"project.name": "x"},
            ["ci_status"],
            ["lint_autofix"],
            "main",
            harness_commands={"test": "pytest", "lint": "ruff check ."},
        )
        assert 'test_command: "pytest"' in yaml
        assert 'lint_command: "ruff check ."' in yaml


# ============================================================
# _build_advanced_yaml
# ============================================================


class TestAdvancedYaml:
    """高级配置文件生成测试。"""

    def test_build_advanced_yaml(self) -> None:
        """advanced 文件需包含全部预期段落。"""
        yaml = _build_advanced_yaml(
            _profile("web-app"),
            ["ci_status", "test_coverage"],
            ["lint_autofix", "format_autofix"],
            harness_commands={"test": "pytest"},
        )
        # 6 大配置段
        assert "agent:" in yaml
        assert "qodercli:" in yaml
        assert "probes:" in yaml
        assert "fixers:" in yaml
        assert "harness:" in yaml
        assert "intelligence:" in yaml
        assert "escalation:" in yaml

        # probes / fixers 列表项
        assert "- ci_status" in yaml
        assert "- test_coverage" in yaml
        assert "- lint_autofix" in yaml
        assert "- format_autofix" in yaml

        # 来自 preset 的字段
        assert "max_turns_fix:" in yaml
        assert "max_turns_develop:" in yaml
        assert "timeout_fix_seconds:" in yaml

        # 高级 harness 参数
        assert "doom_loop_window:" in yaml
        assert "risk_scoring_enabled:" in yaml

        # intelligence/escalation 关键字段
        assert "rca_enabled:" in yaml
        assert "trend_enabled:" in yaml
        assert "max_same_issue_rounds:" in yaml

    def test_build_advanced_yaml_uses_scenario_preset(self) -> None:
        """advanced 文件中的 qodercli 参数应来自当前场景预设。"""
        from vivify.config.presets import get_preset

        yaml = _build_advanced_yaml(
            _profile("docs-only"),
            [],
            [],
            harness_commands={},
        )
        preset = get_preset("docs-only")
        # preset 中至少 max_turns_fix 应在输出中出现
        assert f"max_turns_fix: {preset['max_turns_fix']}" in yaml


# ============================================================
# Smoke test：覆盖所有 ScenarioType
# ============================================================


def test_quick_yaml_supports_all_scenarios() -> None:
    """quick 模板需对所有内置 ScenarioType 可用，不抛异常。"""
    for s in ScenarioType:
        yaml = _build_quick_yaml(
            _profile(s.value),
            {"project.name": "demo"},
            "main",
        )
        assert f'type: "{s.value}"' in yaml
