"""Unit tests for vivify.intelligence.wiki_generator."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vivify.intelligence.wiki_generator import (
    DEFAULT_WIKI_DIR,
    WikiContext,
    generate_wiki,
    load_wiki_context_if_available,
    parse_wiki_metadata,
)


# ---------------------------------------------------------------------------
# WikiContext.to_prompt_block
# ---------------------------------------------------------------------------


class TestWikiContextPromptBlock:
    def test_empty_context_renders_empty_string(self) -> None:
        ctx = WikiContext()
        assert ctx.is_empty()
        assert ctx.to_prompt_block() == ""

    def test_renders_overview_and_files(self) -> None:
        ctx = WikiContext(
            overview="Project does X and Y.",
            source_files=["a.py", "b.py", "c.py"],
            catalogs=["Overview", "API"],
            snippet_count=42,
            source_file_count=3,
            catalog_count=2,
        )
        block = ctx.to_prompt_block()
        assert "项目 Wiki 分析结果" in block
        assert "代码片段索引: 42" in block
        assert "a.py" in block
        assert "b.py" in block
        assert "Overview" in block
        assert "Project does X and Y." in block

    def test_overview_truncation(self) -> None:
        ctx = WikiContext(overview="x" * 5000, source_file_count=0)
        block = ctx.to_prompt_block(max_overview_chars=100)
        # 截断标记存在
        assert "…" in block
        # 不会原样输出 5000 字符
        assert block.count("x") < 500

    def test_source_files_truncation(self) -> None:
        files = [f"file_{i}.py" for i in range(50)]
        ctx = WikiContext(source_files=files, source_file_count=50)
        block = ctx.to_prompt_block(max_source_files=5)
        assert "file_0.py" in block
        assert "以及其他 45 个文件" in block


# ---------------------------------------------------------------------------
# parse_wiki_metadata
# ---------------------------------------------------------------------------


def _write_metadata(repo: Path, payload: dict) -> Path:
    meta = repo / DEFAULT_WIKI_DIR / "meta" / "repowiki-metadata.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps(payload), encoding="utf-8")
    return meta


class TestParseWikiMetadata:
    def test_returns_none_when_metadata_absent(self, tmp_path: Path) -> None:
        assert parse_wiki_metadata(tmp_path) is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        meta = tmp_path / DEFAULT_WIKI_DIR / "meta" / "repowiki-metadata.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text("{not json", encoding="utf-8")
        assert parse_wiki_metadata(tmp_path) is None

    def test_returns_none_for_non_dict_root(self, tmp_path: Path) -> None:
        meta = tmp_path / DEFAULT_WIKI_DIR / "meta" / "repowiki-metadata.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text("[1, 2, 3]", encoding="utf-8")
        assert parse_wiki_metadata(tmp_path) is None

    def test_extracts_full_context(self, tmp_path: Path) -> None:
        _write_metadata(tmp_path, {
            "wiki_overview": {"content": "Hello vivify project!"},
            "source_files": [
                {"path": "vivify/__init__.py", "filename": "__init__.py"},
                {"path": "vivify/cli/main.py", "filename": "main.py"},
                "vivify/legacy.py",  # 兼容字符串
            ],
            "wiki_catalogs": [
                {"name": "项目概述"},
                {"title": "API 设计"},
            ],
            "code_snippets": [{"id": i} for i in range(10)],
        })
        ctx = parse_wiki_metadata(tmp_path)
        assert ctx is not None
        assert ctx.overview == "Hello vivify project!"
        assert ctx.source_files == [
            "vivify/__init__.py",
            "vivify/cli/main.py",
            "vivify/legacy.py",
        ]
        assert ctx.catalogs == ["项目概述", "API 设计"]
        assert ctx.snippet_count == 10
        assert ctx.source_file_count == 3
        assert ctx.catalog_count == 2
        assert ctx.wiki_path == DEFAULT_WIKI_DIR

    def test_overview_string_form(self, tmp_path: Path) -> None:
        _write_metadata(tmp_path, {"wiki_overview": "raw overview text"})
        ctx = parse_wiki_metadata(tmp_path)
        assert ctx is not None
        assert ctx.overview == "raw overview text"

    def test_handles_missing_optional_fields(self, tmp_path: Path) -> None:
        _write_metadata(tmp_path, {})
        ctx = parse_wiki_metadata(tmp_path)
        assert ctx is not None
        assert ctx.is_empty()
        assert ctx.snippet_count == 0
        assert ctx.source_files == []

    def test_load_wiki_context_if_available_silent_on_failure(
        self, tmp_path: Path
    ) -> None:
        # 不存在元数据文件
        assert load_wiki_context_if_available(tmp_path) is None


# ---------------------------------------------------------------------------
# generate_wiki
# ---------------------------------------------------------------------------


class TestGenerateWiki:
    def test_returns_failure_when_binary_missing(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value=None):
            ok, info = generate_wiki(
                tmp_path, qodercli_path="qodercli-not-installed"
            )
        assert ok is False
        assert "not found" in info

    def test_returns_success_on_zero_exit(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value="/usr/bin/qodercli"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="ok", stderr=""
                )
                ok, info = generate_wiki(tmp_path)
        assert ok is True
        assert info == "ok"

    def test_returns_failure_on_nonzero_exit(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value="/usr/bin/qodercli"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=2, stdout="", stderr="boom"
                )
                ok, info = generate_wiki(tmp_path)
        assert ok is False
        assert "exit=2" in info
        assert "boom" in info

    def test_returns_failure_on_timeout(self, tmp_path: Path) -> None:
        import subprocess as _sp
        with patch("shutil.which", return_value="/usr/bin/qodercli"):
            with patch(
                "subprocess.run",
                side_effect=_sp.TimeoutExpired(cmd="qodercli", timeout=1),
            ):
                ok, info = generate_wiki(tmp_path, timeout_seconds=1)
        assert ok is False
        assert "timed out" in info

    def test_invokes_qodercli_with_expected_args(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value="/usr/bin/qodercli"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                generate_wiki(
                    tmp_path,
                    qodercli_path="qodercli",
                    language="zh",
                    permission_mode="bypass_permissions",
                )
                args, kwargs = mock_run.call_args
                cmd = args[0]
                assert cmd[1] == "wiki"
                assert "--repo" in cmd
                assert "--language" in cmd
                assert "zh" in cmd
                assert "--permission-mode" in cmd
                assert "bypass_permissions" in cmd
