"""Tests for knowledge graph models and storage."""

import json
from pathlib import Path

import pytest

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
from vivify.knowledge.storage import KnowledgeStorage


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_node():
    return GraphNode(
        id="module:vivify/kernel",
        type=NodeType.MODULE,
        name="kernel",
        path="vivify/kernel",
        summary="Core event loop and dispatch",
        tags=["core", "loop"],
        complexity=Complexity.COMPLEX,
        responsibility="Orchestrate probes, fixers, and health monitoring",
        exports=["KernelLoop", "dispatch"],
        dependencies=["module:vivify/probes", "module:vivify/fixers"],
        layer="core",
        line_count=450,
        functions=["run", "dispatch", "stop"],
        classes=["KernelLoop"],
    )


@pytest.fixture
def sample_edge():
    return GraphEdge(
        source="module:vivify/kernel",
        target="module:vivify/probes",
        type=EdgeType.DEPENDS_ON,
        weight=0.9,
    )


@pytest.fixture
def sample_metadata():
    return GraphMetadata(
        project_name="vivify",
        description="Self-healing infrastructure",
        languages=["python"],
        frameworks=["fastapi"],
        git_commit_hash="abc123def",
        generated_at="2026-05-28T10:00:00",
        version="1.0.0",
        file_fingerprints={"vivify/kernel/loop.py": "sha256:deadbeef"},
    )


@pytest.fixture
def sample_layer():
    return ArchitectureLayer(
        name="Core Layer",
        description="Core business logic",
        node_ids=["module:vivify/kernel", "module:vivify/probes"],
    )


@pytest.fixture
def sample_convention():
    return CodeConvention(
        category="naming",
        rule="Use snake_case for functions and variables",
        example="def my_function():",
        language="python",
    )


@pytest.fixture
def sample_graph(sample_node, sample_edge, sample_metadata, sample_layer, sample_convention):
    return KnowledgeGraph(
        metadata=sample_metadata,
        nodes=[sample_node],
        edges=[sample_edge],
        layers=[sample_layer],
        conventions=[sample_convention],
    )


# ─── Model Serialization Tests ───────────────────────────────────────────────


class TestGraphNode:
    def test_to_dict(self, sample_node):
        d = sample_node.to_dict()
        assert d["id"] == "module:vivify/kernel"
        assert d["type"] == "module"
        assert d["complexity"] == "complex"
        assert d["tags"] == ["core", "loop"]
        assert d["exports"] == ["KernelLoop", "dispatch"]

    def test_from_dict_roundtrip(self, sample_node):
        d = sample_node.to_dict()
        restored = GraphNode.from_dict(d)
        assert restored.id == sample_node.id
        assert restored.type == sample_node.type
        assert restored.complexity == sample_node.complexity
        assert restored.tags == sample_node.tags
        assert restored.exports == sample_node.exports
        assert restored.line_count == sample_node.line_count

    def test_from_dict_minimal(self):
        data = {"id": "file:test.py", "type": "file", "name": "test", "path": "test.py"}
        node = GraphNode.from_dict(data)
        assert node.summary == ""
        assert node.tags == []
        assert node.complexity == Complexity.SIMPLE
        assert node.line_count == 0

    def test_json_roundtrip(self, sample_node):
        json_str = json.dumps(sample_node.to_dict())
        restored = GraphNode.from_dict(json.loads(json_str))
        assert restored.id == sample_node.id


class TestGraphEdge:
    def test_to_dict(self, sample_edge):
        d = sample_edge.to_dict()
        assert d["source"] == "module:vivify/kernel"
        assert d["target"] == "module:vivify/probes"
        assert d["type"] == "depends_on"
        assert d["weight"] == 0.9

    def test_from_dict_roundtrip(self, sample_edge):
        d = sample_edge.to_dict()
        restored = GraphEdge.from_dict(d)
        assert restored.source == sample_edge.source
        assert restored.type == sample_edge.type
        assert restored.weight == sample_edge.weight

    def test_default_weight(self):
        data = {"source": "a", "target": "b", "type": "calls"}
        edge = GraphEdge.from_dict(data)
        assert edge.weight == 1.0


class TestArchitectureLayer:
    def test_roundtrip(self, sample_layer):
        d = sample_layer.to_dict()
        restored = ArchitectureLayer.from_dict(d)
        assert restored.name == sample_layer.name
        assert restored.node_ids == sample_layer.node_ids

    def test_empty_node_ids(self):
        data = {"name": "Util", "description": "Utilities"}
        layer = ArchitectureLayer.from_dict(data)
        assert layer.node_ids == []


class TestCodeConvention:
    def test_roundtrip(self, sample_convention):
        d = sample_convention.to_dict()
        restored = CodeConvention.from_dict(d)
        assert restored.category == "naming"
        assert restored.rule == sample_convention.rule
        assert restored.example == sample_convention.example

    def test_minimal(self):
        data = {"category": "imports", "rule": "Sort imports alphabetically"}
        conv = CodeConvention.from_dict(data)
        assert conv.example == ""
        assert conv.language == ""


class TestGraphMetadata:
    def test_roundtrip(self, sample_metadata):
        d = sample_metadata.to_dict()
        restored = GraphMetadata.from_dict(d)
        assert restored.project_name == "vivify"
        assert restored.git_commit_hash == "abc123def"
        assert restored.file_fingerprints == {"vivify/kernel/loop.py": "sha256:deadbeef"}

    def test_empty(self):
        meta = GraphMetadata.from_dict({})
        assert meta.project_name == ""
        assert meta.version == "1.0.0"
        assert meta.file_fingerprints == {}


# ─── KnowledgeGraph Query Tests ──────────────────────────────────────────────


class TestKnowledgeGraph:
    def test_to_dict_from_dict_roundtrip(self, sample_graph):
        d = sample_graph.to_dict()
        restored = KnowledgeGraph.from_dict(d)
        assert len(restored.nodes) == 1
        assert len(restored.edges) == 1
        assert len(restored.layers) == 1
        assert len(restored.conventions) == 1
        assert restored.metadata.project_name == "vivify"

    def test_json_roundtrip(self, sample_graph):
        json_str = json.dumps(sample_graph.to_dict(), ensure_ascii=False)
        data = json.loads(json_str)
        restored = KnowledgeGraph.from_dict(data)
        assert restored.nodes[0].id == "module:vivify/kernel"

    def test_get_node_found(self, sample_graph):
        node = sample_graph.get_node("module:vivify/kernel")
        assert node is not None
        assert node.name == "kernel"

    def test_get_node_not_found(self, sample_graph):
        assert sample_graph.get_node("module:nonexistent") is None

    def test_get_module_nodes(self, sample_graph):
        modules = sample_graph.get_module_nodes()
        assert len(modules) == 1
        assert modules[0].type == NodeType.MODULE

    def test_get_module_nodes_mixed(self):
        graph = KnowledgeGraph(
            nodes=[
                GraphNode(id="module:a", type=NodeType.MODULE, name="a", path="a"),
                GraphNode(id="file:b.py", type=NodeType.FILE, name="b", path="b.py"),
                GraphNode(id="module:c", type=NodeType.MODULE, name="c", path="c"),
            ]
        )
        modules = graph.get_module_nodes()
        assert len(modules) == 2

    def test_get_edges_for(self, sample_graph):
        edges = sample_graph.get_edges_for("module:vivify/kernel")
        assert len(edges) == 1
        # Also find edges where node is target
        edges_target = sample_graph.get_edges_for("module:vivify/probes")
        assert len(edges_target) == 1

    def test_get_edges_for_no_match(self, sample_graph):
        edges = sample_graph.get_edges_for("module:nonexistent")
        assert edges == []

    def test_get_dependencies(self, sample_graph):
        deps = sample_graph.get_dependencies("module:vivify/kernel")
        assert deps == ["module:vivify/probes"]

    def test_get_dependencies_no_deps(self, sample_graph):
        deps = sample_graph.get_dependencies("module:vivify/probes")
        assert deps == []

    def test_empty_graph(self):
        graph = KnowledgeGraph()
        assert graph.get_node("x") is None
        assert graph.get_module_nodes() == []
        assert graph.get_edges_for("x") == []
        assert graph.get_dependencies("x") == []
        d = graph.to_dict()
        restored = KnowledgeGraph.from_dict(d)
        assert len(restored.nodes) == 0

    def test_from_dict_empty(self):
        graph = KnowledgeGraph.from_dict({})
        assert graph.metadata.project_name == ""
        assert graph.nodes == []


# ─── Storage Tests ────────────────────────────────────────────────────────────


class TestKnowledgeStorage:
    def test_ensure_dir(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        storage.ensure_dir()
        assert (tmp_path / ".vivify" / "knowledge").is_dir()
        assert (tmp_path / ".vivify" / "knowledge" / "modules").is_dir()

    def test_save_and_load_graph(self, tmp_path, sample_graph):
        storage = KnowledgeStorage(tmp_path)
        storage.save_graph(sample_graph)

        loaded = storage.load_graph()
        assert loaded is not None
        assert len(loaded.nodes) == 1
        assert loaded.nodes[0].id == "module:vivify/kernel"
        assert loaded.metadata.project_name == "vivify"

    def test_load_graph_not_exists(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        assert storage.load_graph() is None

    def test_load_graph_invalid_json(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        storage.ensure_dir()
        (tmp_path / ".vivify" / "knowledge" / "graph.json").write_text("not json")
        assert storage.load_graph() is None

    def test_save_and_load_module_detail(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        detail = {"name": "kernel", "files": ["loop.py", "dispatch.py"], "description": "Core"}
        storage.save_module_detail("kernel", detail)

        loaded = storage.load_module_detail("kernel")
        assert loaded == detail

    def test_load_module_detail_not_exists(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        assert storage.load_module_detail("nonexistent") is None

    def test_save_and_load_meta(self, tmp_path, sample_metadata):
        storage = KnowledgeStorage(tmp_path)
        storage.save_meta(sample_metadata)

        loaded = storage.load_meta()
        assert loaded is not None
        assert loaded.project_name == "vivify"
        assert loaded.git_commit_hash == "abc123def"

    def test_load_meta_not_exists(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        assert storage.load_meta() is None

    def test_save_and_load_conventions(self, tmp_path, sample_convention):
        storage = KnowledgeStorage(tmp_path)
        conventions = [sample_convention]
        storage.save_conventions(conventions)

        loaded = storage.load_conventions()
        assert len(loaded) == 1
        assert loaded[0].category == "naming"
        assert loaded[0].rule == sample_convention.rule

    def test_load_conventions_not_exists(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        assert storage.load_conventions() == []

    def test_get_last_commit_hash(self, tmp_path, sample_metadata):
        storage = KnowledgeStorage(tmp_path)
        # No meta yet
        assert storage.get_last_commit_hash() == ""
        # Save meta
        storage.save_meta(sample_metadata)
        assert storage.get_last_commit_hash() == "abc123def"

    def test_needs_update_no_meta(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        assert storage.needs_update("abc123") is True

    def test_needs_update_empty_hash(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        assert storage.needs_update("") is True

    def test_needs_update_same_hash(self, tmp_path, sample_metadata):
        storage = KnowledgeStorage(tmp_path)
        storage.save_meta(sample_metadata)
        assert storage.needs_update("abc123def") is False

    def test_needs_update_different_hash(self, tmp_path, sample_metadata):
        storage = KnowledgeStorage(tmp_path)
        storage.save_meta(sample_metadata)
        assert storage.needs_update("newcommithash") is True

    def test_save_graph_creates_valid_json(self, tmp_path, sample_graph):
        storage = KnowledgeStorage(tmp_path)
        storage.save_graph(sample_graph)
        graph_path = tmp_path / ".vivify" / "knowledge" / "graph.json"
        # Verify it's valid JSON
        data = json.loads(graph_path.read_text(encoding="utf-8"))
        assert "metadata" in data
        assert "nodes" in data
        assert "edges" in data

    def test_multiple_modules(self, tmp_path):
        storage = KnowledgeStorage(tmp_path)
        storage.save_module_detail("kernel", {"name": "kernel"})
        storage.save_module_detail("probes", {"name": "probes"})

        assert storage.load_module_detail("kernel") == {"name": "kernel"}
        assert storage.load_module_detail("probes") == {"name": "probes"}
