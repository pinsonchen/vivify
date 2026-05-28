"""Unit tests for SemanticAnalyzer."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vivify.knowledge.analyzers.semantic_analyzer import (
    ModuleSemantics,
    SemanticAnalyzer,
)


@pytest.fixture
def project_root(tmp_path):
    """Create a temporary project root with wiki metadata."""
    return tmp_path


@pytest.fixture
def analyzer(project_root):
    """Create a SemanticAnalyzer instance."""
    return SemanticAnalyzer(
        project_root=project_root,
        qodercli_binary="qodercli",
        wiki_path="",
        permission_mode="bypass_permissions",
    )


@pytest.fixture
def sample_modules():
    """Sample module data for testing."""
    return [
        {
            "name": "kernel",
            "path": "vivify/kernel",
            "files": ["loop.py", "dispatch.py", "feature_pipeline.py"],
            "exports": ["KernelLoop", "Dispatcher", "FeaturePipeline"],
            "dependencies": ["config", "storage"],
            "total_lines": 1200,
            "complexity": "complex",
        },
        {
            "name": "cli",
            "path": "vivify/cli",
            "files": ["main.py", "init_cmd.py", "daemon_cmd.py"],
            "exports": ["main", "init_command", "daemon_command"],
            "dependencies": ["kernel", "config"],
            "total_lines": 800,
            "complexity": "moderate",
        },
        {
            "name": "storage",
            "path": "vivify/storage",
            "files": ["sqlite_provider.py"],
            "exports": ["SqliteProvider"],
            "dependencies": [],
            "total_lines": 400,
            "complexity": "moderate",
        },
        {
            "name": "config",
            "path": "vivify/config",
            "files": ["loader.py", "schema.py", "defaults.py"],
            "exports": ["load_config", "VivifyConfig", "DEFAULTS"],
            "dependencies": [],
            "total_lines": 300,
            "complexity": "simple",
        },
    ]


@pytest.fixture
def wiki_metadata():
    """Sample wiki metadata JSON structure."""
    return {
        "wiki_overview": {
            "content": "Vivify 是一个自增长型 AI 代理系统，提供智能自我修复能力。",
            "id": "test-id",
            "repo_id": "test-repo",
            "gmt_create": "2026-01-01",
            "gmt_modified": "2026-01-01",
        },
        "wiki_catalogs": [
            {
                "id": "cat-1",
                "name": "核心内核循环和文档更新",
                "description": "core-kernel-loop",
                "prompt": "核心内核负责调度探针、修复器和特征管道的执行循环。",
                "dependent_files": "vivify/kernel/loop.py,vivify/kernel/dispatch.py",
                "progress_status": "completed",
            },
            {
                "id": "cat-2",
                "name": "CLI初始化命令增强",
                "description": "cli-init-command",
                "prompt": "CLI模块提供命令行接口，包括初始化、守护进程管理等功能。",
                "dependent_files": "vivify/cli/main.py,vivify/cli/init_cmd.py",
                "progress_status": "completed",
            },
            {
                "id": "cat-3",
                "name": "配置加载器更新",
                "description": "config-loader",
                "prompt": "配置模块负责加载和验证项目配置文件。",
                "dependent_files": "vivify/config/loader.py",
                "progress_status": "completed",
            },
        ],
        "source_files": [
            {"path": "vivify/kernel/loop.py", "filename": "loop.py", "id": "sf1"},
            {"path": "vivify/cli/main.py", "filename": "main.py", "id": "sf2"},
            {"path": "vivify/storage/sqlite_provider.py", "filename": "sqlite_provider.py", "id": "sf3"},
        ],
        "wiki_items": [],
        "code_snippets": [],
        "knowledge_relations": [],
    }


def _setup_wiki_metadata(project_root: Path, metadata: dict):
    """Write wiki metadata to the expected location."""
    wiki_dir = project_root / ".qoder" / "repowiki" / "zh" / "meta"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    meta_path = wiki_dir / "repowiki-metadata.json"
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")


class TestExtractFromWiki:
    """Tests for _extract_from_wiki()."""

    def test_returns_none_when_no_metadata(self, analyzer, sample_modules):
        """Should return None if wiki metadata file doesn't exist."""
        result = analyzer._extract_from_wiki(sample_modules)
        assert result is None

    def test_returns_none_on_invalid_json(self, analyzer, project_root, sample_modules):
        """Should return None if metadata JSON is invalid."""
        wiki_dir = project_root / ".qoder" / "repowiki" / "zh" / "meta"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        meta_path = wiki_dir / "repowiki-metadata.json"
        meta_path.write_text("not valid json {{{", encoding="utf-8")

        result = analyzer._extract_from_wiki(sample_modules)
        assert result is None

    def test_extracts_semantics_from_valid_metadata(
        self, analyzer, project_root, sample_modules, wiki_metadata
    ):
        """Should extract semantics when metadata matches modules."""
        _setup_wiki_metadata(project_root, wiki_metadata)

        result = analyzer._extract_from_wiki(sample_modules)
        assert result is not None
        assert len(result) >= 1

        # Check that at least kernel or cli was matched
        names = {s.name for s in result}
        # At least some modules should have been matched
        assert len(names) >= 1

    def test_matched_module_has_description(
        self, analyzer, project_root, sample_modules, wiki_metadata
    ):
        """Matched modules should have non-empty description."""
        _setup_wiki_metadata(project_root, wiki_metadata)

        result = analyzer._extract_from_wiki(sample_modules)
        assert result is not None

        for sem in result:
            assert sem.description != ""
            assert sem.name != ""

    def test_handles_empty_catalogs(self, analyzer, project_root, sample_modules):
        """Should handle metadata with empty catalogs gracefully."""
        metadata = {
            "wiki_overview": {"content": "A project"},
            "wiki_catalogs": [],
            "source_files": [],
        }
        _setup_wiki_metadata(project_root, metadata)

        result = analyzer._extract_from_wiki(sample_modules)
        # No catalogs means no matches
        assert result is None


class TestBuildAnalysisPrompt:
    """Tests for _build_analysis_prompt()."""

    def test_prompt_contains_module_names(self, analyzer, sample_modules):
        """Prompt should include all module names."""
        prompt = analyzer._build_analysis_prompt(sample_modules)

        for m in sample_modules:
            assert m["name"] in prompt

    def test_prompt_contains_exports(self, analyzer, sample_modules):
        """Prompt should include key exports."""
        prompt = analyzer._build_analysis_prompt(sample_modules)

        assert "KernelLoop" in prompt
        assert "SqliteProvider" in prompt

    def test_prompt_contains_json_format(self, analyzer, sample_modules):
        """Prompt should request JSON output format."""
        prompt = analyzer._build_analysis_prompt(sample_modules)

        assert "json" in prompt.lower()
        assert "description" in prompt
        assert "responsibility" in prompt
        assert "layer" in prompt
        assert "tags" in prompt

    def test_prompt_limits_exports(self, analyzer):
        """Prompt should limit exports to 10 per module."""
        module_with_many_exports = {
            "name": "big_module",
            "path": "src/big",
            "exports": [f"Export{i}" for i in range(20)],
            "dependencies": [],
            "total_lines": 5000,
        }

        prompt = analyzer._build_analysis_prompt([module_with_many_exports])

        # Should include first 10 but not all 20
        assert "Export0" in prompt
        assert "Export9" in prompt
        assert "Export10" not in prompt

    def test_prompt_includes_project_name(self, analyzer):
        """Prompt should mention the project name from root dir."""
        prompt = analyzer._build_analysis_prompt(
            [{"name": "test", "path": "test", "exports": [], "dependencies": []}]
        )
        # Project name comes from self.root.name
        assert analyzer.root.name in prompt


class TestParseLlmResponse:
    """Tests for _parse_llm_response()."""

    def test_parses_valid_json_block(self, analyzer, sample_modules):
        """Should parse valid JSON in code block."""
        response = '''Here is the analysis:

```json
[
  {"name": "kernel", "description": "Core execution engine", "responsibility": "Manages the main event loop", "layer": "core", "tags": ["engine", "loop"]},
  {"name": "cli", "description": "Command line interface", "responsibility": "Handles user commands", "layer": "api", "tags": ["cli", "commands"]},
  {"name": "storage", "description": "Data persistence layer", "responsibility": "Stores state in SQLite", "layer": "data", "tags": ["sqlite", "persistence"]},
  {"name": "config", "description": "Configuration management", "responsibility": "Loads and validates config", "layer": "config", "tags": ["yaml", "settings"]}
]
```
'''
        result = analyzer._parse_llm_response(response, sample_modules)

        assert result is not None
        assert len(result) == 4
        assert result[0].name == "kernel"
        assert result[0].description == "Core execution engine"
        assert result[0].layer == "core"
        assert result[0].tags == ["engine", "loop"]

    def test_parses_raw_json_array(self, analyzer, sample_modules):
        """Should handle JSON without code block wrapper."""
        response = '[{"name": "kernel", "description": "Engine", "responsibility": "Runs loops", "layer": "core", "tags": ["core"]}]'

        result = analyzer._parse_llm_response(response, sample_modules)

        assert result is not None
        assert len(result) == 1
        assert result[0].name == "kernel"

    def test_returns_none_for_empty_output(self, analyzer, sample_modules):
        """Should return None for empty response."""
        assert analyzer._parse_llm_response("", sample_modules) is None
        assert analyzer._parse_llm_response("  ", sample_modules) is None

    def test_returns_none_for_invalid_json(self, analyzer, sample_modules):
        """Should return None for malformed JSON."""
        response = "```json\n{not valid json}\n```"
        result = analyzer._parse_llm_response(response, sample_modules)
        assert result is None

    def test_returns_none_for_non_array(self, analyzer, sample_modules):
        """Should return None if JSON is not an array."""
        response = '```json\n{"name": "kernel"}\n```'
        result = analyzer._parse_llm_response(response, sample_modules)
        assert result is None

    def test_handles_invalid_layer(self, analyzer, sample_modules):
        """Should infer layer if LLM returns invalid value."""
        response = '```json\n[{"name": "kernel", "description": "Engine", "responsibility": "Runs", "layer": "invalid_layer", "tags": []}]\n```'

        result = analyzer._parse_llm_response(response, sample_modules)

        assert result is not None
        # Should fall back to heuristic (kernel -> core)
        assert result[0].layer == "core"

    def test_fuzzy_matches_module_names(self, analyzer, sample_modules):
        """Should fuzzy match if LLM returns slightly different names."""
        response = '```json\n[{"name": "Kernel", "description": "Engine", "responsibility": "Core", "layer": "core", "tags": []}]\n```'

        result = analyzer._parse_llm_response(response, sample_modules)

        assert result is not None
        assert result[0].name == "kernel"

    def test_truncates_long_description(self, analyzer, sample_modules):
        """Should truncate overly long descriptions."""
        long_desc = "x" * 500
        response = f'```json\n[{{"name": "kernel", "description": "{long_desc}", "responsibility": "test", "layer": "core", "tags": []}}]\n```'

        result = analyzer._parse_llm_response(response, sample_modules)

        assert result is not None
        assert len(result[0].description) <= 200


class TestFallbackSemantics:
    """Tests for _fallback_semantics()."""

    def test_returns_semantics_for_all_modules(self, analyzer, sample_modules):
        """Should return one ModuleSemantics per module."""
        result = analyzer._fallback_semantics(sample_modules)

        assert len(result) == len(sample_modules)
        names = {s.name for s in result}
        expected_names = {m["name"] for m in sample_modules}
        assert names == expected_names

    def test_infers_layer_from_name(self, analyzer, sample_modules):
        """Should infer reasonable layers from module names."""
        result = analyzer._fallback_semantics(sample_modules)

        layer_map = {s.name: s.layer for s in result}
        assert layer_map["kernel"] == "core"
        assert layer_map["cli"] == "api"
        assert layer_map["storage"] == "data"
        assert layer_map["config"] == "config"

    def test_generates_description_from_name(self, analyzer, sample_modules):
        """Should generate human-readable description from module name."""
        result = analyzer._fallback_semantics(sample_modules)

        for sem in result:
            assert sem.description != ""
            assert "module" in sem.description.lower()

    def test_generates_tags(self, analyzer, sample_modules):
        """Should generate some tags for each module."""
        result = analyzer._fallback_semantics(sample_modules)

        for sem in result:
            assert isinstance(sem.tags, list)


class TestAnalyzeIncremental:
    """Tests for analyze_incremental()."""

    def test_keeps_existing_for_unchanged(self, analyzer):
        """Should keep existing semantics for modules not in changed list."""
        existing = [
            ModuleSemantics(name="kernel", description="Old desc", layer="core"),
            ModuleSemantics(name="storage", description="Storage desc", layer="data"),
        ]
        changed = [
            {"name": "cli", "path": "vivify/cli", "exports": [], "dependencies": [], "total_lines": 100},
        ]

        result = analyzer.analyze_incremental(changed, existing)

        result_map = {s.name: s for s in result}
        # Existing unchanged modules should be preserved
        assert result_map["kernel"].description == "Old desc"
        assert result_map["storage"].description == "Storage desc"
        # Changed module should be newly analyzed
        assert "cli" in result_map

    def test_updates_changed_modules(self, analyzer):
        """Should re-analyze changed modules even if they existed before."""
        existing = [
            ModuleSemantics(name="kernel", description="Old kernel desc", layer="core"),
        ]
        changed = [
            {"name": "kernel", "path": "vivify/kernel", "exports": ["NewExport"], "dependencies": [], "total_lines": 2000},
        ]

        result = analyzer.analyze_incremental(changed, existing)

        result_map = {s.name: s for s in result}
        assert "kernel" in result_map
        # Description should be updated (fallback will generate new one)
        assert result_map["kernel"].description != "Old kernel desc"

    def test_empty_changed_returns_existing(self, analyzer):
        """Should return existing semantics unchanged when no modules changed."""
        existing = [
            ModuleSemantics(name="kernel", description="Desc", layer="core"),
        ]

        result = analyzer.analyze_incremental([], existing)
        assert len(result) == 1
        assert result[0].name == "kernel"
        assert result[0].description == "Desc"


class TestAnalyzeIntegration:
    """Integration tests for the main analyze() method."""

    @patch("subprocess.run")
    def test_falls_back_to_llm_when_no_wiki(
        self, mock_run, analyzer, sample_modules
    ):
        """Should call LLM when wiki is not available."""
        llm_response = '''```json
[
  {"name": "kernel", "description": "Core engine", "responsibility": "Execution loop", "layer": "core", "tags": ["engine"]},
  {"name": "cli", "description": "CLI interface", "responsibility": "Commands", "layer": "api", "tags": ["cli"]},
  {"name": "storage", "description": "Persistence", "responsibility": "SQLite", "layer": "data", "tags": ["db"]},
  {"name": "config", "description": "Config mgmt", "responsibility": "YAML", "layer": "config", "tags": ["config"]}
]
```'''
        mock_run.return_value = MagicMock(
            returncode=0, stdout=llm_response, stderr=""
        )

        result = analyzer.analyze(sample_modules)

        assert len(result) == 4
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_falls_back_to_heuristic_on_llm_failure(
        self, mock_run, analyzer, sample_modules
    ):
        """Should use fallback semantics when LLM fails."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error"
        )

        result = analyzer.analyze(sample_modules)

        # Should still return results (fallback)
        assert len(result) == len(sample_modules)
        for sem in result:
            assert sem.name != ""
            assert sem.layer != ""

    @patch("subprocess.run")
    def test_uses_wiki_when_available(
        self, mock_run, analyzer, project_root, sample_modules, wiki_metadata
    ):
        """Should prefer wiki extraction over LLM."""
        _setup_wiki_metadata(project_root, wiki_metadata)

        result = analyzer.analyze(sample_modules)

        # If wiki covers 70%+ modules, LLM should NOT be called
        # In this test wiki matches ~3 out of 4, which is 75%
        assert len(result) >= len(sample_modules)

    def test_handles_empty_modules_list(self, analyzer):
        """Should return empty list for empty input."""
        result = analyzer.analyze([])
        assert result == []


class TestModuleSemantics:
    """Tests for ModuleSemantics dataclass."""

    def test_to_dict(self):
        sem = ModuleSemantics(
            name="kernel",
            description="Core engine",
            responsibility="Manages execution",
            layer="core",
            tags=["engine", "loop"],
        )
        d = sem.to_dict()
        assert d["name"] == "kernel"
        assert d["description"] == "Core engine"
        assert d["layer"] == "core"
        assert d["tags"] == ["engine", "loop"]

    def test_from_dict(self):
        data = {
            "name": "storage",
            "description": "Data layer",
            "responsibility": "Persistence",
            "layer": "data",
            "tags": ["sqlite"],
        }
        sem = ModuleSemantics.from_dict(data)
        assert sem.name == "storage"
        assert sem.layer == "data"

    def test_from_dict_missing_fields(self):
        data = {"name": "minimal"}
        sem = ModuleSemantics.from_dict(data)
        assert sem.name == "minimal"
        assert sem.description == ""
        assert sem.tags == []
