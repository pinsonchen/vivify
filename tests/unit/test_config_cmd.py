"""Tests for ``vivify config`` subcommands."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from vivify.cli import config_cmd
from vivify.cli.config_cmd import (
    FIELD_EXPLANATIONS,
    _cmd_diff,
    _cmd_explain,
    _cmd_show,
    _cmd_validate,
    _find_diffs,
)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------


def _ns(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace from kwargs."""
    return argparse.Namespace(**kwargs)


@pytest.fixture
def chdir_tmp(tmp_path: Path):
    """Switch CWD to ``tmp_path`` for the test."""
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)


def _write_minimal_config(path: Path, *, with_token: bool = True) -> None:
    """Write a minimal valid .vivify.yml."""
    body = (
        "version: 1\n"
        "mode: daemon\n"
        "project:\n"
        '  name: "demo"\n'
        '  type: "web-app"\n'
        "harness:\n"
        "  enabled: true\n"
        '  test_command: "pytest"\n'
    )
    if with_token:
        body += "github:\n  token: \"ghp_xxx\"\n"
    (path / ".vivify.yml").write_text(body, encoding="utf-8")


# ------------------------------------------------------------
# explain
# ------------------------------------------------------------


class TestCmdExplain:
    """``vivify config explain`` 行为。"""

    def test_cmd_explain_known_key(self, capsys) -> None:
        """已知 key 返回 0 并打印解释。"""
        rc = _cmd_explain(_ns(key="harness.doom_loop_window"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "harness.doom_loop_window" in out
        assert "滑动窗口" in out

    def test_cmd_explain_unknown_key(self, capsys) -> None:
        """未知且无任何模糊匹配的 key 返回 1。"""
        rc = _cmd_explain(_ns(key="totally.random.unknown.key.zzz"))
        out = capsys.readouterr().out
        assert rc == 1
        assert "未找到" in out

    def test_cmd_explain_fuzzy_match(self, capsys) -> None:
        """无精确匹配但子串命中应返回相关键并退出码为 0。"""
        # 'harness' 不是精确 key（精确的是 harness.enabled 等），但是子串
        rc = _cmd_explain(_ns(key="doom_loop"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "harness.doom_loop_window" in out
        assert "harness.doom_loop_threshold" in out

    def test_cmd_explain_list_all(self, capsys) -> None:
        """不传 key 时列出全部条目。"""
        rc = _cmd_explain(_ns(key=None))
        out = capsys.readouterr().out
        assert rc == 0
        # 抽样检查若干代表性 key
        assert "version" in out
        assert "harness.enabled" in out
        # 总数提示
        assert str(len(FIELD_EXPLANATIONS)) in out


# ------------------------------------------------------------
# validate
# ------------------------------------------------------------


class TestCmdValidate:
    """``vivify config validate`` 行为。"""

    def test_cmd_validate_no_file(self, chdir_tmp, capsys) -> None:
        """配置文件不存在时返回 1。"""
        rc = _cmd_validate(_ns())
        err = capsys.readouterr().err
        assert rc == 1
        assert ".vivify.yml" in err

    def test_cmd_validate_valid(self, chdir_tmp, capsys) -> None:
        """有效配置返回 0。"""
        _write_minimal_config(chdir_tmp)
        rc = _cmd_validate(_ns())
        out = capsys.readouterr().out
        assert rc == 0
        assert "格式正确" in out


# ------------------------------------------------------------
# diff
# ------------------------------------------------------------


class TestCmdDiff:
    """``vivify config diff`` 行为。"""

    def test_cmd_diff_no_file(self, chdir_tmp, capsys) -> None:
        """无配置时返回 1。"""
        rc = _cmd_diff(_ns())
        err = capsys.readouterr().err
        assert rc == 1
        assert ".vivify.yml" in err

    def test_cmd_diff_finds_differences(self, chdir_tmp, capsys) -> None:
        """diff 能找出用户自定义字段。"""
        _write_minimal_config(chdir_tmp)
        rc = _cmd_diff(_ns())
        out = capsys.readouterr().out
        assert rc == 0
        # project.name 是自定义值，应被检出
        assert "project.name" in out
        assert "demo" in out


# ------------------------------------------------------------
# show
# ------------------------------------------------------------


class TestCmdShow:
    """``vivify config show`` 行为。"""

    def test_cmd_show_json(self, chdir_tmp, capsys) -> None:
        """JSON 格式输出可被解析。"""
        _write_minimal_config(chdir_tmp)
        rc = _cmd_show(_ns(format="json"))
        out = capsys.readouterr().out
        assert rc == 0
        import json

        data = json.loads(out)
        assert isinstance(data, dict)
        assert data.get("project", {}).get("name") == "demo"

    def test_cmd_show_yaml(self, chdir_tmp, capsys) -> None:
        """默认 yaml 格式应包含项目名。"""
        _write_minimal_config(chdir_tmp)
        rc = _cmd_show(_ns(format="yaml"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "demo" in out


# ------------------------------------------------------------
# _find_diffs
# ------------------------------------------------------------


class TestFindDiffs:
    """``_find_diffs`` 递归差异计算。"""

    def test_find_diffs_recursive(self) -> None:
        """嵌套 dict 中的差异应被正确定位。"""
        user = {
            "project": {"name": "demo", "type": "web-app"},
            "agent": {"qodercli": {"max_turns_fix": 99}},
        }
        default = {
            "project": {"name": "", "type": "generic"},
            "agent": {"qodercli": {"max_turns_fix": 30}},
        }
        diffs = _find_diffs(user, default, "")
        paths = {d[0] for d in diffs}
        assert "project.name" in paths
        assert "project.type" in paths
        assert "agent.qodercli.max_turns_fix" in paths

    def test_find_diffs_no_difference(self) -> None:
        """完全相同的 dict 应返回空。"""
        same = {"a": 1, "nested": {"b": 2}}
        diffs = _find_diffs(same, dict(same), "")
        assert diffs == []


# ------------------------------------------------------------
# run dispatcher
# ------------------------------------------------------------


def test_run_no_action_prints_help(capsys) -> None:
    """未指定子命令时打印用法并返回 0。"""
    rc = config_cmd.run(_ns(config_action=None))
    out = capsys.readouterr().out
    assert rc == 0
    assert "用法" in out
