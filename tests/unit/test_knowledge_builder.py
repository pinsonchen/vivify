"""Tests for knowledge graph builder."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from vivify.knowledge.builder import KnowledgeBuilder
from vivify.knowledge.models import (
    ArchitectureLayer,
    CodeConvention,
    Complexity,
    EdgeType,
    GraphEdge,
    GraphMetadata,
    GraphNode,
    KnowledgeGraph,
    NodeType,
)
from vivify.knowledge.analyzers.static_analyzer import (
    FileInfo,
    ModuleInfo,
    StructuralGraph,
)
from vivify.knowledge.analyzers.semantic_analyzer import ModuleSemantics


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project structure for testing."""
    # Create a minimal Python package
    pkg_dir = tmp_path / "mypackage"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "core.py").write_text(
        '"""Core module."""\n\n\nclass Engine:\n    pass\n\n\ndef run():\n    pass\n'
    )
    (pkg_dir / "utils.py").write_text(
        '"""Utilities."""\n\nimport os\n\n\ndef helper():\n    pass\n'
    )

    # Create a sub-package
    sub_dir = pkg_dir / "sub"
    sub_dir.mkdir()
    (sub_dir / "__init__.py").write_text("")
    (sub_dir / "worker.py").write_text(
        "from mypackage.core import Engine\n\n\nclass Worker:\n    pass\n"
    )

    # Create git dir marker (for git hash tests)
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def sample_file_info():
    return FileInfo(
        path="vivify/kernel/loop.py",
        language="python",
        line_count=200,
        imports=["vivify.probes", "logging"],
        exports=["KernelLoop", "run_loop"],
        classes=["KernelLoop"],
        functions=["run_loop", "_internal_helper"],
        decorators=["dataclass"],
        fingerprint="abc123",
    )


@pytest.fixture
def sample_module_info(sample_file_info):
    return ModuleInfo(
        name="kernel",
        path="vivify/kernel",
        files=[sample_file_info],
        total_lines=200,
        complexity="moderate",
        internal_imports=["vivify.probes"],
        external_imports=["logging"],
    )


@pytest.fixture
def sample_structural_graph(sample_module_info):
    probes_file = FileInfo(
        path="vivify/probes/runner.py",
        language="python",
        line_count=150,
        imports=["vivify.models"],
        exports=["ProbeRunner", "run_probes"],
        classes=["ProbeRunner"],
        functions=["run_probes"],
        decorators=[],
        fingerprint="def456",
    )
    probes_mod = ModuleInfo(
        name="probes",
        path="vivify/probes",
        files=[probes_file],
        total_lines=150,
        complexity="simple",
        internal_imports=["vivify.models"],
        external_imports=[],
    )
    return StructuralGraph(
        modules=[sample_module_info, probes_mod],
        edges=[("kernel", "probes", "imports")],
        file_fingerprints={
            "vivify/kernel/loop.py": "abc123",
            "vivify/probes/runner.py": "def456",
        },
        project_root="/tmp/test",
        languages=["python"],
    )


@pytest.fixture
def builder(tmp_project):
    return KnowledgeBuilder(
        project_root=tmp_project,
        qodercli_binary="qodercli",
        timeout=60,
    )


# ─── _structural_to_graph Tests ─────────────────────────────────────────────


class TestStructuralToGraph:
    def test_creates_module_nodes(self, builder, sample_structural_graph):
        graph = builder._structural_to_graph(sample_structural_graph)
        module_nodes = graph.get_module_nodes()
        assert len(module_nodes) == 2
        names = {n.name for n in module_nodes}
        assert "kernel" in names
        assert "probes" in names

    def test_creates_file_nodes(self, builder, sample_structural_graph):
        graph = builder._structural_to_graph(sample_structural_graph)
        file_nodes = [n for n in graph.nodes if n.type == NodeType.FILE]
        assert len(file_nodes) == 2
        paths = {n.path for n in file_nodes}
        assert "vivify/kernel/loop.py" in paths
        assert "vivify/probes/runner.py" in paths

    def test_creates_class_nodes_for_public_classes(self, builder, sample_structural_graph):
        graph = builder._structural_to_graph(sample_structural_graph)
        class_nodes = [n for n in graph.nodes if n.type == NodeType.CLASS]
        assert len(class_nodes) == 2
        names = {n.name for n in class_nodes}
        assert "KernelLoop" in names
        assert "ProbeRunner" in names

    def test_no_nodes_for_private_classes(self, builder):
        file_info = FileInfo(
            path="mod/priv.py",
            language="python",
            line_count=50,
            imports=[],
            exports=[],
            classes=["_PrivateClass", "PublicClass"],
            functions=["_private_fn", "public_fn"],
            decorators=[],
            fingerprint="xyz",
        )
        mod = ModuleInfo(
            name="mod", path="mod", files=[file_info],
            total_lines=50, complexity="simple",
            internal_imports=[], external_imports=[],
        )
        structural = StructuralGraph(
            modules=[mod], edges=[], file_fingerprints={},
            project_root="/tmp", languages=["python"],
        )
        graph = builder._structural_to_graph(structural)
        class_nodes = [n for n in graph.nodes if n.type == NodeType.CLASS]
        assert len(class_nodes) == 1
        assert class_nodes[0].name == "PublicClass"

    def test_contains_edges_module_to_file(self, builder, sample_structural_graph):
        graph = builder._structural_to_graph(sample_structural_graph)
        contains_edges = [e for e in graph.edges if e.type == EdgeType.CONTAINS]
        # 2 modules * 1 file each + 2 classes
        assert len(contains_edges) == 4

    def test_imports_edges(self, builder, sample_structural_graph):
        graph = builder._structural_to_graph(sample_structural_graph)
        import_edges = [e for e in graph.edges if e.type == EdgeType.IMPORTS]
        assert len(import_edges) == 1
        assert import_edges[0].source == "module:vivify/kernel"
        assert import_edges[0].target == "module:vivify/probes"

    def test_module_exports_collected(self, builder, sample_structural_graph):
        graph = builder._structural_to_graph(sample_structural_graph)
        kernel = graph.get_node("module:vivify/kernel")
        assert kernel is not None
        assert "KernelLoop" in kernel.exports
        assert "run_loop" in kernel.exports

    def test_module_complexity_preserved(self, builder, sample_structural_graph):
        graph = builder._structural_to_graph(sample_structural_graph)
        kernel = graph.get_node("module:vivify/kernel")
        assert kernel.complexity == Complexity.MODERATE

    def test_empty_structural_graph(self, builder):
        structural = StructuralGraph(
            modules=[], edges=[], file_fingerprints={},
            project_root="/tmp", languages=[],
        )
        graph = builder._structural_to_graph(structural)
        assert graph.nodes == []
        assert graph.edges == []


# ─── _extract_conventions Tests ──────────────────────────────────────────────


class TestExtractConventions:
    def test_detects_snake_case(self, builder):
        files = [
            FileInfo(
                path="mod/a.py", language="python", line_count=10,
                imports=[], exports=[], classes=[],
                functions=["get_user", "save_data", "run_task", "process_item"],
                decorators=[], fingerprint="a",
            ),
        ]
        mod = ModuleInfo(
            name="mod", path="mod", files=files, total_lines=10,
            complexity="simple", internal_imports=[], external_imports=[],
        )
        structural = StructuralGraph(
            modules=[mod], edges=[], file_fingerprints={},
            project_root=str(builder.root), languages=["python"],
        )
        conventions = builder._extract_conventions(structural)
        naming = [c for c in conventions if c.category == "naming"]
        assert any("snake_case" in c.rule for c in naming)

    def test_detects_absolute_imports(self, builder):
        files = [
            FileInfo(
                path="mod/a.py", language="python", line_count=10,
                imports=["vivify.kernel", "vivify.probes", "os", "sys"],
                exports=[], classes=[], functions=[],
                decorators=[], fingerprint="a",
            ),
        ]
        mod = ModuleInfo(
            name="mod", path="mod", files=files, total_lines=10,
            complexity="simple", internal_imports=[], external_imports=[],
        )
        structural = StructuralGraph(
            modules=[mod], edges=[], file_fingerprints={},
            project_root=str(builder.root), languages=["python"],
        )
        conventions = builder._extract_conventions(structural)
        import_convs = [c for c in conventions if c.category == "imports"]
        assert any("absolute" in c.rule.lower() for c in import_convs)

    def test_detects_pascal_case_classes(self, builder):
        files = [
            FileInfo(
                path="mod/a.py", language="python", line_count=10,
                imports=[], exports=[],
                classes=["MyClass", "AnotherClass", "BigService"],
                functions=[], decorators=[], fingerprint="a",
            ),
        ]
        mod = ModuleInfo(
            name="mod", path="mod", files=files, total_lines=10,
            complexity="simple", internal_imports=[], external_imports=[],
        )
        structural = StructuralGraph(
            modules=[mod], edges=[], file_fingerprints={},
            project_root=str(builder.root), languages=["python"],
        )
        conventions = builder._extract_conventions(structural)
        naming = [c for c in conventions if c.category == "naming"]
        assert any("PascalCase" in c.rule for c in naming)

    def test_empty_modules_no_crash(self, builder):
        structural = StructuralGraph(
            modules=[], edges=[], file_fingerprints={},
            project_root=str(builder.root), languages=[],
        )
        conventions = builder._extract_conventions(structural)
        assert isinstance(conventions, list)


# ─── _derive_layers Tests ────────────────────────────────────────────────────


class TestDeriveLayers:
    def test_groups_by_layer(self, builder):
        graph = KnowledgeGraph(
            nodes=[
                GraphNode(
                    id="module:api", type=NodeType.MODULE,
                    name="api", path="api", layer="api",
                ),
                GraphNode(
                    id="module:core", type=NodeType.MODULE,
                    name="core", path="core", layer="core",
                ),
                GraphNode(
                    id="module:utils", type=NodeType.MODULE,
                    name="utils", path="utils", layer="util",
                ),
            ]
        )
        layers = builder._derive_layers(graph)
        assert len(layers) == 3
        names = {l.name for l in layers}
        assert "Api Layer" in names
        assert "Core Layer" in names
        assert "Util Layer" in names

    def test_empty_layer_defaults_to_other(self, builder):
        graph = KnowledgeGraph(
            nodes=[
                GraphNode(
                    id="module:unknown", type=NodeType.MODULE,
                    name="unknown", path="unknown", layer="",
                ),
            ]
        )
        layers = builder._derive_layers(graph)
        assert len(layers) == 1
        assert layers[0].name == "Other Layer"

    def test_layer_node_ids(self, builder):
        graph = KnowledgeGraph(
            nodes=[
                GraphNode(
                    id="module:a", type=NodeType.MODULE,
                    name="a", path="a", layer="core",
                ),
                GraphNode(
                    id="module:b", type=NodeType.MODULE,
                    name="b", path="b", layer="core",
                ),
            ]
        )
        layers = builder._derive_layers(graph)
        assert len(layers) == 1
        assert set(layers[0].node_ids) == {"module:a", "module:b"}

    def test_non_module_nodes_ignored(self, builder):
        graph = KnowledgeGraph(
            nodes=[
                GraphNode(
                    id="module:a", type=NodeType.MODULE,
                    name="a", path="a", layer="core",
                ),
                GraphNode(
                    id="file:a/b.py", type=NodeType.FILE,
                    name="b.py", path="a/b.py", layer="core",
                ),
            ]
        )
        layers = builder._derive_layers(graph)
        assert len(layers) == 1
        assert layers[0].node_ids == ["module:a"]


# ─── build_full Tests ────────────────────────────────────────────────────────


class TestBuildFull:
    @patch.object(KnowledgeBuilder, "_get_current_git_hash", return_value="abc123")
    def test_full_pipeline(self, mock_hash, builder, sample_structural_graph):
        """Test complete build pipeline with mocked analyzers."""
        builder.static_analyzer = MagicMock()
        builder.static_analyzer.analyze.return_value = sample_structural_graph

        builder.semantic_analyzer = MagicMock()
        builder.semantic_analyzer.analyze.return_value = [
            ModuleSemantics(
                name="kernel",
                description="Core event loop",
                responsibility="Orchestrates execution",
                layer="core",
                tags=["core", "loop"],
            ),
            ModuleSemantics(
                name="probes",
                description="Health probes",
                responsibility="Monitors system health",
                layer="core",
                tags=["monitoring"],
            ),
        ]

        graph = builder.build_full()

        assert len(graph.nodes) > 0
        assert len(graph.edges) > 0
        assert graph.metadata.project_name != ""
        assert graph.metadata.git_commit_hash == "abc123"
        assert len(graph.layers) > 0

        # Verify static analyzer was called
        builder.static_analyzer.analyze.assert_called_once()
        # Verify semantic analyzer was called
        builder.semantic_analyzer.analyze.assert_called_once()

    @patch.object(KnowledgeBuilder, "_get_current_git_hash", return_value="abc123")
    def test_timeout_returns_partial(self, mock_hash, builder, sample_structural_graph):
        """Test that timeout returns partial graph."""
        builder.timeout = 0  # Immediate timeout
        builder.static_analyzer = MagicMock()
        builder.static_analyzer.analyze.return_value = sample_structural_graph

        graph = builder.build_full()
        # Should still have structural nodes from phase 1
        assert len(graph.nodes) > 0

    @patch.object(KnowledgeBuilder, "_get_current_git_hash", return_value="abc123")
    def test_semantic_failure_degrades_gracefully(self, mock_hash, builder, sample_structural_graph):
        """Test that semantic failure doesn't block the build."""
        builder.static_analyzer = MagicMock()
        builder.static_analyzer.analyze.return_value = sample_structural_graph

        builder.semantic_analyzer = MagicMock()
        builder.semantic_analyzer.analyze.side_effect = RuntimeError("LLM unavailable")

        graph = builder.build_full()
        # Should still have structural data
        assert len(graph.nodes) > 0
        assert graph.metadata.project_name != ""

    def test_static_analysis_failure_returns_empty(self, builder):
        """Test that static analysis failure returns empty graph."""
        builder.static_analyzer = MagicMock()
        builder.static_analyzer.analyze.side_effect = RuntimeError("Parse error")

        graph = builder.build_full()
        assert len(graph.nodes) == 0

    @patch.object(KnowledgeBuilder, "_get_current_git_hash", return_value="abc123")
    def test_persists_to_storage(self, mock_hash, builder, sample_structural_graph):
        """Test that graph is persisted."""
        builder.static_analyzer = MagicMock()
        builder.static_analyzer.analyze.return_value = sample_structural_graph
        builder.semantic_analyzer = MagicMock()
        builder.semantic_analyzer.analyze.return_value = []

        builder.build_full()

        # Verify storage files exist
        knowledge_dir = builder.root / ".vivify" / "knowledge"
        assert (knowledge_dir / "graph.json").exists()
        assert (knowledge_dir / "meta.json").exists()


# ─── build_incremental Tests ─────────────────────────────────────────────────


class TestBuildIncremental:
    @patch.object(KnowledgeBuilder, "_get_current_git_hash", return_value="newcommit")
    @patch.object(KnowledgeBuilder, "_get_changed_files", return_value=["vivify/kernel/loop.py"])
    def test_incremental_with_changes(
        self, mock_files, mock_hash, builder, sample_structural_graph
    ):
        """Test incremental update with changed files."""
        # Set up existing graph
        existing = KnowledgeGraph(
            metadata=GraphMetadata(git_commit_hash="oldcommit"),
            nodes=[
                GraphNode(
                    id="module:vivify/kernel", type=NodeType.MODULE,
                    name="kernel", path="vivify/kernel",
                ),
            ],
            edges=[],
        )
        builder.storage = MagicMock()
        builder.storage.load_graph.return_value = existing
        builder.storage.needs_update.return_value = True
        builder.storage.get_last_commit_hash.return_value = "oldcommit"

        builder.static_analyzer = MagicMock()
        builder.static_analyzer.analyze_incremental.return_value = sample_structural_graph

        builder.semantic_analyzer = MagicMock()
        builder.semantic_analyzer.analyze_incremental.return_value = []

        result = builder.build_incremental()

        assert result is not None
        assert result.metadata.git_commit_hash == "newcommit"
        builder.static_analyzer.analyze_incremental.assert_called_once_with(
            ["vivify/kernel/loop.py"]
        )

    @patch.object(KnowledgeBuilder, "_get_current_git_hash", return_value="same_hash")
    def test_no_update_needed(self, mock_hash, builder):
        """Test that no update returns None."""
        existing = KnowledgeGraph(
            metadata=GraphMetadata(git_commit_hash="same_hash"),
        )
        builder.storage = MagicMock()
        builder.storage.load_graph.return_value = existing
        builder.storage.needs_update.return_value = False

        result = builder.build_incremental()
        assert result is None

    @patch.object(KnowledgeBuilder, "_get_current_git_hash", return_value="newcommit")
    @patch.object(KnowledgeBuilder, "_get_changed_files", return_value=[])
    def test_no_changed_files(self, mock_files, mock_hash, builder):
        """Test empty changed files returns None."""
        existing = KnowledgeGraph(
            metadata=GraphMetadata(git_commit_hash="oldcommit"),
        )
        builder.storage = MagicMock()
        builder.storage.load_graph.return_value = existing
        builder.storage.needs_update.return_value = True
        builder.storage.get_last_commit_hash.return_value = "oldcommit"

        result = builder.build_incremental()
        assert result is None

    def test_no_existing_graph_triggers_full_build(self, builder, sample_structural_graph):
        """Test that missing existing graph triggers full build."""
        builder.storage = MagicMock()
        builder.storage.load_graph.return_value = None

        builder.static_analyzer = MagicMock()
        builder.static_analyzer.analyze.return_value = sample_structural_graph
        builder.semantic_analyzer = MagicMock()
        builder.semantic_analyzer.analyze.return_value = []

        with patch.object(builder, "_get_current_git_hash", return_value="abc"):
            result = builder.build_incremental()
            assert result is not None
            builder.static_analyzer.analyze.assert_called_once()


# ─── _merge_incremental Tests ────────────────────────────────────────────────


class TestMergeIncremental:
    def test_replaces_affected_module(self, builder):
        """Test that affected modules are replaced."""
        existing = KnowledgeGraph(
            nodes=[
                GraphNode(
                    id="module:vivify/kernel", type=NodeType.MODULE,
                    name="kernel", path="vivify/kernel",
                ),
                GraphNode(
                    id="file:vivify/kernel/old.py", type=NodeType.FILE,
                    name="old.py", path="vivify/kernel/old.py",
                ),
                GraphNode(
                    id="module:vivify/probes", type=NodeType.MODULE,
                    name="probes", path="vivify/probes",
                ),
            ],
            edges=[
                GraphEdge(
                    source="module:vivify/kernel",
                    target="file:vivify/kernel/old.py",
                    type=EdgeType.CONTAINS,
                ),
            ],
        )

        new_file = FileInfo(
            path="vivify/kernel/new.py", language="python", line_count=100,
            imports=[], exports=["NewClass"], classes=["NewClass"],
            functions=[], decorators=[], fingerprint="new",
        )
        new_mod = ModuleInfo(
            name="kernel", path="vivify/kernel",
            files=[new_file], total_lines=100, complexity="simple",
            internal_imports=[], external_imports=[],
        )
        structural = StructuralGraph(
            modules=[new_mod], edges=[], file_fingerprints={},
            project_root="/tmp", languages=["python"],
        )

        result = builder._merge_incremental(
            existing, structural, ["vivify/kernel/new.py"]
        )

        # Probes module should be unchanged
        assert result.get_node("module:vivify/probes") is not None
        # Old kernel file should be gone
        assert result.get_node("file:vivify/kernel/old.py") is None
        # New kernel file should exist
        assert result.get_node("file:vivify/kernel/new.py") is not None

    def test_preserves_unaffected_modules(self, builder):
        """Test that unaffected modules are preserved."""
        existing = KnowledgeGraph(
            nodes=[
                GraphNode(
                    id="module:vivify/agents", type=NodeType.MODULE,
                    name="agents", path="vivify/agents",
                    summary="Agent management",
                ),
            ],
            edges=[],
        )
        structural = StructuralGraph(
            modules=[], edges=[], file_fingerprints={},
            project_root="/tmp", languages=["python"],
        )
        result = builder._merge_incremental(existing, structural, [])
        assert result.get_node("module:vivify/agents") is not None
        assert result.get_node("module:vivify/agents").summary == "Agent management"


# ─── Git Helper Tests ────────────────────────────────────────────────────────


class TestGitHelpers:
    def test_get_current_git_hash_success(self, builder):
        """Test git hash retrieval in a real git repo."""
        # This test might pass or fail depending on environment
        # Use mock for deterministic behavior
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="abc123def456\n"
            )
            result = builder._get_current_git_hash()
            assert result == "abc123def456"

    def test_get_current_git_hash_failure(self, builder):
        """Test git hash failure returns empty string."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = builder._get_current_git_hash()
            assert result == ""

    def test_get_current_git_hash_exception(self, builder):
        """Test git hash exception returns empty string."""
        with patch("subprocess.run", side_effect=OSError("no git")):
            result = builder._get_current_git_hash()
            assert result == ""

    def test_get_changed_files(self, builder):
        """Test changed files parsing."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="vivify/kernel/loop.py\nvivify/probes/runner.py\n",
            )
            result = builder._get_changed_files("old", "new")
            assert result == ["vivify/kernel/loop.py", "vivify/probes/runner.py"]

    def test_get_changed_files_empty(self, builder):
        """Test empty diff."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = builder._get_changed_files("old", "new")
            assert result == []

    def test_get_changed_files_no_hashes(self, builder):
        """Test missing hashes returns empty."""
        assert builder._get_changed_files("", "new") == []
        assert builder._get_changed_files("old", "") == []


# ─── Helper Method Tests ─────────────────────────────────────────────────────


class TestHelperMethods:
    def test_collect_module_exports(self, builder, sample_module_info):
        exports = builder._collect_module_exports(sample_module_info)
        assert "KernelLoop" in exports
        assert "run_loop" in exports

    def test_collect_module_exports_deduplicates(self, builder):
        file1 = FileInfo(
            path="a.py", language="python", line_count=10,
            imports=[], exports=["Shared", "Unique1"],
            classes=[], functions=[], decorators=[], fingerprint="",
        )
        file2 = FileInfo(
            path="b.py", language="python", line_count=10,
            imports=[], exports=["Shared", "Unique2"],
            classes=[], functions=[], decorators=[], fingerprint="",
        )
        mod = ModuleInfo(
            name="test", path="test", files=[file1, file2],
            total_lines=20, complexity="simple",
            internal_imports=[], external_imports=[],
        )
        exports = builder._collect_module_exports(mod)
        assert exports.count("Shared") == 1
        assert "Unique1" in exports
        assert "Unique2" in exports

    def test_is_snake_case(self, builder):
        assert builder._is_snake_case("get_user") is True
        assert builder._is_snake_case("run") is True
        assert builder._is_snake_case("_private_fn") is True
        assert builder._is_snake_case("getUserName") is False
        assert builder._is_snake_case("GetUser") is False

    def test_is_pascal_case(self, builder):
        assert builder._is_pascal_case("MyClass") is True
        assert builder._is_pascal_case("A") is True
        assert builder._is_pascal_case("my_class") is False
        assert builder._is_pascal_case("myClass") is False

    def test_detect_frameworks(self, builder):
        file_info = FileInfo(
            path="app.py", language="python", line_count=10,
            imports=["fastapi", "pydantic", "logging"],
            exports=[], classes=[], functions=[], decorators=[], fingerprint="",
        )
        mod = ModuleInfo(
            name="app", path="app", files=[file_info],
            total_lines=10, complexity="simple",
            internal_imports=[], external_imports=["fastapi", "pydantic"],
        )
        structural = StructuralGraph(
            modules=[mod], edges=[], file_fingerprints={},
            project_root="/tmp", languages=["python"],
        )
        frameworks = builder._detect_frameworks(structural)
        assert "FastAPI" in frameworks
        assert "Pydantic" in frameworks

    def test_find_module_path(self, builder, sample_structural_graph):
        path = builder._find_module_path("kernel", sample_structural_graph)
        assert path == "vivify/kernel"

    def test_find_module_path_not_found(self, builder, sample_structural_graph):
        path = builder._find_module_path("nonexistent", sample_structural_graph)
        assert path == "nonexistent"


# ─── _build_metadata Tests ───────────────────────────────────────────────────


class TestBuildMetadata:
    @patch.object(KnowledgeBuilder, "_get_current_git_hash", return_value="meta_hash")
    def test_metadata_fields(self, mock_hash, builder, sample_structural_graph):
        meta = builder._build_metadata(sample_structural_graph)
        assert meta.project_name != ""
        assert meta.git_commit_hash == "meta_hash"
        assert meta.generated_at != ""
        assert "python" in meta.languages
        assert meta.version == "1.0.0"
        assert meta.file_fingerprints == sample_structural_graph.file_fingerprints


# ─── End-to-End Test ─────────────────────────────────────────────────────────


class TestEndToEnd:
    def test_build_on_real_project(self, tmp_project):
        """Run build_full on a minimal real project structure."""
        builder = KnowledgeBuilder(
            project_root=tmp_project,
            qodercli_binary="nonexistent_binary",  # Will fail gracefully
            timeout=30,
        )

        # Mock semantic analyzer to avoid LLM calls
        builder.semantic_analyzer = MagicMock()
        builder.semantic_analyzer.analyze.return_value = []

        with patch.object(builder, "_get_current_git_hash", return_value="e2e_hash"):
            graph = builder.build_full()

        # Should have discovered the mypackage module
        module_nodes = graph.get_module_nodes()
        module_names = {n.name for n in module_nodes}
        assert "mypackage" in module_names or len(module_nodes) > 0

        # Should have file nodes
        file_nodes = [n for n in graph.nodes if n.type == NodeType.FILE]
        assert len(file_nodes) > 0

        # Metadata should be populated
        assert graph.metadata.project_name != ""
        assert graph.metadata.git_commit_hash == "e2e_hash"

        # Should persist
        knowledge_dir = tmp_project / ".vivify" / "knowledge"
        assert (knowledge_dir / "graph.json").exists()
        assert (knowledge_dir / "meta.json").exists()

    def test_build_persists_loadable_graph(self, tmp_project):
        """Test that persisted graph can be loaded back."""
        builder = KnowledgeBuilder(
            project_root=tmp_project,
            qodercli_binary="nonexistent_binary",
            timeout=30,
        )
        builder.semantic_analyzer = MagicMock()
        builder.semantic_analyzer.analyze.return_value = []

        with patch.object(builder, "_get_current_git_hash", return_value="load_hash"):
            original = builder.build_full()

        # Load back
        from vivify.knowledge.storage import KnowledgeStorage

        storage = KnowledgeStorage(tmp_project)
        loaded = storage.load_graph()
        assert loaded is not None
        assert len(loaded.nodes) == len(original.nodes)
        assert loaded.metadata.git_commit_hash == "load_hash"
