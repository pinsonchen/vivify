"""Tests for vivify.knowledge.context_provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from vivify.knowledge.context_provider import (
    KnowledgeContextProvider,
    get_knowledge_context,
)
from vivify.knowledge.models import (
    ArchitectureLayer,
    CodeConvention,
    Complexity,
    GraphEdge,
    GraphMetadata,
    GraphNode,
    EdgeType,
    KnowledgeGraph,
    NodeType,
)
from vivify.knowledge.storage import KnowledgeStorage


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sample_graph() -> KnowledgeGraph:
    metadata = GraphMetadata(
        project_name="vivify",
        description="self-healing pipeline",
        languages=["python"],
        frameworks=["fastapi"],
    )
    kernel = GraphNode(
        id="module:vivify/kernel",
        type=NodeType.MODULE,
        name="kernel",
        path="vivify/kernel",
        summary="Core event loop",
        responsibility="Orchestrate probes, fixers, feature pipeline",
        exports=["KernelLoop", "FeaturePipeline", "dispatch"],
        dependencies=["module:vivify/probes"],
        tags=["core", "loop", "feature"],
        complexity=Complexity.COMPLEX,
        layer="core",
    )
    probes = GraphNode(
        id="module:vivify/probes",
        type=NodeType.MODULE,
        name="probes",
        path="vivify/probes",
        summary="Probe registry and runners",
        responsibility="Detect issues via builtin probes",
        exports=["ProbeRunner", "RuleEngine"],
        tags=["probe", "detect"],
    )
    knowledge = GraphNode(
        id="module:vivify/knowledge",
        type=NodeType.MODULE,
        name="knowledge",
        path="vivify/knowledge",
        summary="Knowledge graph storage",
        responsibility="Build and maintain knowledge graph",
        exports=["KnowledgeBuilder", "KnowledgeStorage"],
        tags=["knowledge", "graph"],
    )
    layer = ArchitectureLayer(
        name="Core Layer",
        description="Kernel loop and dispatch",
        node_ids=["module:vivify/kernel"],
    )
    return KnowledgeGraph(
        metadata=metadata,
        nodes=[kernel, probes, knowledge],
        layers=[layer],
    )


@pytest.fixture
def populated_root(project_root: Path, sample_graph: KnowledgeGraph) -> Path:
    storage = KnowledgeStorage(project_root)
    storage.save_graph(sample_graph)
    storage.save_conventions(
        [
            CodeConvention(category="naming", rule="Use snake_case for functions"),
            CodeConvention(category="imports", rule="Group stdlib/third-party/local"),
        ]
    )
    return project_root


# ── L1 Overview ─────────────────────────────────────────────────────────────


def test_l1_overview_includes_project_and_modules(populated_root: Path):
    provider = KnowledgeContextProvider(populated_root)
    context = provider.get_context_for_feature("any feature", "")
    assert "项目知识上下文" in context
    assert "vivify" in context
    assert "self-healing pipeline" in context
    assert "vivify/kernel" in context
    assert "vivify/probes" in context
    assert "Core Layer" in context


def test_l1_overview_empty_graph(project_root: Path):
    provider = KnowledgeContextProvider(project_root)
    # No graph saved → should return empty.
    assert provider.get_context_for_feature("anything") == ""


def test_get_context_for_goal_only_overview(populated_root: Path):
    provider = KnowledgeContextProvider(populated_root)
    ctx = provider.get_context_for_goal("Goal X", "do X")
    assert "项目" in ctx
    # goal context should NOT include conventions block
    assert "代码规范" not in ctx


# ── L2 Relevance ────────────────────────────────────────────────────────────


def test_l2_related_modules_matches_relevant(populated_root: Path):
    provider = KnowledgeContextProvider(populated_root)
    ctx = provider.get_context_for_feature(
        "Improve feature pipeline kernel loop",
        "Fix dispatch ordering in feature pipeline",
    )
    assert "相关模块详情" in ctx
    # kernel should rank top because both 'feature pipeline' and 'kernel' match
    assert "kernel" in ctx


def test_l2_skips_when_no_overlap(populated_root: Path):
    provider = KnowledgeContextProvider(populated_root)
    # Random unrelated text — no token overlap with module attrs
    ctx = provider.get_context_for_feature("xyzzy plover quux", "wibble wobble")
    # L1 still appears, but L2 should not
    assert "相关模块详情" not in ctx


def test_compute_relevance_returns_zero_for_empty_query(populated_root: Path):
    provider = KnowledgeContextProvider(populated_root)
    graph = provider._get_graph()
    module = graph.get_module_nodes()[0]
    score = provider._compute_relevance(module, set())
    assert score == 0.0


def test_compute_relevance_higher_for_better_match(populated_root: Path):
    provider = KnowledgeContextProvider(populated_root)
    graph = provider._get_graph()
    kernel = next(m for m in graph.get_module_nodes() if m.name == "kernel")
    probes = next(m for m in graph.get_module_nodes() if m.name == "probes")

    query = provider._tokenize("kernel loop dispatch")
    s_kernel = provider._compute_relevance(kernel, query)
    s_probes = provider._compute_relevance(probes, query)
    assert s_kernel > s_probes


# ── Tokenization ────────────────────────────────────────────────────────────


def test_tokenize_camel_case(project_root: Path):
    provider = KnowledgeContextProvider(project_root)
    tokens = provider._tokenize("KnowledgeContextProvider")
    assert "knowledge" in tokens
    assert "context" in tokens
    assert "provider" in tokens


def test_tokenize_snake_case(project_root: Path):
    provider = KnowledgeContextProvider(project_root)
    tokens = provider._tokenize("feature_pipeline_runner")
    assert "feature" in tokens
    assert "pipeline" in tokens
    assert "runner" in tokens


def test_tokenize_chinese(project_root: Path):
    provider = KnowledgeContextProvider(project_root)
    tokens = provider._tokenize("知识图谱注入")
    # Should produce CJK shingles
    assert any("\u4e00" <= ch <= "\u9fff" for t in tokens for ch in t)
    assert "知识图谱注入" in tokens or "知识" in tokens


def test_tokenize_empty(project_root: Path):
    provider = KnowledgeContextProvider(project_root)
    assert provider._tokenize("") == set()


# ── Conventions Block ───────────────────────────────────────────────────────


def test_conventions_block_in_feature_context(populated_root: Path):
    provider = KnowledgeContextProvider(populated_root)
    ctx = provider.get_context_for_feature("any feature")
    assert "代码规范" in ctx
    assert "snake_case" in ctx


def test_conventions_block_truncates_to_budget(project_root: Path):
    provider = KnowledgeContextProvider(project_root)
    convs = [
        CodeConvention(category="naming", rule="x" * 50)
        for _ in range(50)
    ]
    block = provider._build_conventions_block(convs, max_chars=200)
    assert len(block) <= 250  # allow header overhead
    assert block.startswith("**代码规范**")


def test_conventions_block_empty(project_root: Path):
    provider = KnowledgeContextProvider(project_root)
    assert provider._build_conventions_block([]) == ""


# ── Token Budget ────────────────────────────────────────────────────────────


def test_token_budget_limits_output(populated_root: Path):
    provider = KnowledgeContextProvider(populated_root)
    ctx = provider.get_context_for_feature(
        "kernel feature pipeline",
        "fix dispatch",
        max_tokens=200,  # very tight budget
    )
    # Char count should remain bounded (~ tokens * 4 + header overhead)
    assert len(ctx) < 200 * 4 + 200


# ── Convenience Function / Exception Safety ─────────────────────────────────


def test_get_knowledge_context_returns_empty_when_no_graph(project_root: Path):
    assert get_knowledge_context(project_root, "feat", "desc") == ""


def test_get_knowledge_context_works_with_populated(populated_root: Path):
    out = get_knowledge_context(populated_root, "kernel feature", "")
    assert "项目知识上下文" in out


def test_get_knowledge_context_exception_safe(monkeypatch, project_root: Path):
    """If the provider blows up, the convenience function returns ''."""
    from vivify.knowledge import context_provider as cp

    class ExplodingProvider:
        def __init__(self, *a, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr(cp, "KnowledgeContextProvider", ExplodingProvider)
    assert cp.get_knowledge_context(project_root, "x", "y") == ""


# ── Caching ─────────────────────────────────────────────────────────────────


def test_graph_load_is_cached(populated_root: Path, monkeypatch):
    provider = KnowledgeContextProvider(populated_root)
    calls = {"n": 0}
    real_load = provider.storage.load_graph

    def counting_load():
        calls["n"] += 1
        return real_load()

    monkeypatch.setattr(provider.storage, "load_graph", counting_load)
    provider.get_context_for_feature("a")
    provider.get_context_for_feature("b")
    assert calls["n"] == 1


# ── Agent Fallback ──────────────────────────────────────────────────────────


def test_agent_falls_back_to_wiki_when_graph_missing(monkeypatch, project_root: Path):
    """``_augment_prompt_with_knowledge`` must call wiki fallback when graph is absent."""
    from vivify.agents.qodercli_agent import QoderCliAgent, QoderCliConfig

    agent = QoderCliAgent(QoderCliConfig())
    called = {"wiki": False}

    def fake_wiki(prompt: str, workspace: Path) -> str:
        called["wiki"] = True
        return f"WIKI::{prompt}"

    monkeypatch.setattr(agent, "_augment_prompt_with_wiki", fake_wiki)
    out = agent._augment_prompt_with_knowledge("hello", project_root)
    assert called["wiki"] is True
    assert out == "WIKI::hello"


def test_agent_uses_knowledge_when_graph_present(populated_root: Path):
    from vivify.agents.qodercli_agent import QoderCliAgent, QoderCliConfig

    agent = QoderCliAgent(QoderCliConfig())
    out = agent._augment_prompt_with_knowledge(
        "implement kernel loop tweak",
        populated_root,
        feature_title="kernel feature pipeline",
    )
    assert "项目知识上下文" in out
    assert "implement kernel loop tweak" in out


# ── recommend_files (Task #87) ──────────────────────────────────────────


def _file_node(
    path: str,
    *,
    name: str | None = None,
    line_count: int = 50,
) -> GraphNode:
    return GraphNode(
        id=f"file:{path}",
        type=NodeType.FILE,
        name=name or path.rsplit("/", 1)[-1],
        path=path,
        line_count=line_count,
    )


def _module_node(
    path: str,
    *,
    name: str | None = None,
    summary: str = "",
    tags: list[str] | None = None,
) -> GraphNode:
    return GraphNode(
        id=f"module:{path}",
        type=NodeType.MODULE,
        name=name or path.rsplit("/", 1)[-1],
        path=path,
        summary=summary,
        tags=tags or [],
    )


def _write(root: Path, rel: str, lines: int = 5) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(f"line {i}" for i in range(lines)) + "\n", encoding="utf-8")
    return p


class TestRecommendFiles:
    """Tests for KnowledgeContextProvider.recommend_files()."""

    def test_returns_empty_when_no_graph(self, project_root: Path):
        provider = KnowledgeContextProvider(project_root)
        assert provider.recommend_files("any feature", project_root) == []

    def test_returns_empty_for_empty_query(
        self, project_root: Path, sample_graph: KnowledgeGraph,
    ):
        storage = KnowledgeStorage(project_root)
        storage.save_graph(sample_graph)
        provider = KnowledgeContextProvider(project_root)
        assert provider.recommend_files("", project_root) == []

    def test_recommends_core_files_by_edge_count(self, project_root: Path):
        # Module + two real files; "core.py" connected to two callees → higher degree.
        kernel_mod = _module_node(
            "vivify/kernel", summary="kernel feature pipeline core",
            tags=["kernel", "feature"],
        )
        core_file = _file_node("vivify/kernel/core.py")
        helper_file = _file_node("vivify/kernel/helper.py")
        callee_a = _file_node("vivify/kernel/util_a.py")
        callee_b = _file_node("vivify/kernel/util_b.py")

        graph = KnowledgeGraph(
            metadata=GraphMetadata(project_name="vivify"),
            nodes=[kernel_mod, core_file, helper_file, callee_a, callee_b],
            edges=[
                GraphEdge(source=kernel_mod.id, target=core_file.id, type=EdgeType.CONTAINS),
                GraphEdge(source=kernel_mod.id, target=helper_file.id, type=EdgeType.CONTAINS),
                GraphEdge(source=kernel_mod.id, target=callee_a.id, type=EdgeType.CONTAINS),
                GraphEdge(source=kernel_mod.id, target=callee_b.id, type=EdgeType.CONTAINS),
                GraphEdge(source=core_file.id, target=callee_a.id, type=EdgeType.CALLS),
                GraphEdge(source=core_file.id, target=callee_b.id, type=EdgeType.CALLS),
            ],
        )
        KnowledgeStorage(project_root).save_graph(graph)
        for rel in (
            "vivify/kernel/core.py",
            "vivify/kernel/helper.py",
            "vivify/kernel/util_a.py",
            "vivify/kernel/util_b.py",
        ):
            _write(project_root, rel)

        provider = KnowledgeContextProvider(project_root)
        result = provider.recommend_files(
            "kernel feature pipeline", project_root, max_files=2,
        )
        # core.py should rank first because it has more edges (1 CONTAINS-in + 2 CALLS-out)
        # than helper/util files.
        assert len(result) == 2
        assert result[0].name == "core.py"

    def test_filters_test_files(self, project_root: Path):
        kernel_mod = _module_node(
            "vivify/kernel", summary="kernel feature pipeline", tags=["kernel"],
        )
        core = _file_node("vivify/kernel/loop.py")
        test_prefixed = _file_node("vivify/kernel/test_loop.py")
        in_tests_dir = _file_node("tests/test_kernel.py")

        graph = KnowledgeGraph(
            metadata=GraphMetadata(),
            nodes=[kernel_mod, core, test_prefixed, in_tests_dir],
            edges=[
                GraphEdge(source=kernel_mod.id, target=core.id, type=EdgeType.CONTAINS),
                GraphEdge(source=kernel_mod.id, target=test_prefixed.id, type=EdgeType.CONTAINS),
                GraphEdge(source=kernel_mod.id, target=in_tests_dir.id, type=EdgeType.CONTAINS),
                # Boost test files' edge degree so they would rank top *if not filtered*.
                GraphEdge(source=test_prefixed.id, target=core.id, type=EdgeType.CALLS),
                GraphEdge(source=test_prefixed.id, target=in_tests_dir.id, type=EdgeType.CALLS),
                GraphEdge(source=in_tests_dir.id, target=core.id, type=EdgeType.CALLS),
            ],
        )
        KnowledgeStorage(project_root).save_graph(graph)
        for rel in (
            "vivify/kernel/loop.py",
            "vivify/kernel/test_loop.py",
            "tests/test_kernel.py",
        ):
            _write(project_root, rel)

        provider = KnowledgeContextProvider(project_root)
        result = provider.recommend_files(
            "kernel feature pipeline", project_root, max_files=5,
        )
        names = [p.name for p in result]
        assert "loop.py" in names
        assert "test_loop.py" not in names
        assert "test_kernel.py" not in names

    def test_filters_nonexistent_files(self, project_root: Path):
        kernel_mod = _module_node(
            "vivify/kernel", summary="kernel feature", tags=["kernel"],
        )
        present = _file_node("vivify/kernel/present.py")
        ghost = _file_node("vivify/kernel/ghost.py")
        graph = KnowledgeGraph(
            metadata=GraphMetadata(),
            nodes=[kernel_mod, present, ghost],
            edges=[
                GraphEdge(source=kernel_mod.id, target=present.id, type=EdgeType.CONTAINS),
                GraphEdge(source=kernel_mod.id, target=ghost.id, type=EdgeType.CONTAINS),
            ],
        )
        KnowledgeStorage(project_root).save_graph(graph)
        _write(project_root, "vivify/kernel/present.py")
        # Note: ghost.py is intentionally NOT created on disk.

        provider = KnowledgeContextProvider(project_root)
        result = provider.recommend_files(
            "kernel feature", project_root, max_files=5,
        )
        names = [p.name for p in result]
        assert names == ["present.py"]

    def test_filters_large_files(self, project_root: Path):
        kernel_mod = _module_node(
            "vivify/kernel", summary="kernel feature", tags=["kernel"],
        )
        # line_count >= 500 ⇒ should be filtered. Make degree higher to ensure
        # it would rank first if not filtered.
        big = _file_node("vivify/kernel/big.py", line_count=600)
        small = _file_node("vivify/kernel/small.py", line_count=10)
        graph = KnowledgeGraph(
            metadata=GraphMetadata(),
            nodes=[kernel_mod, big, small],
            edges=[
                GraphEdge(source=kernel_mod.id, target=big.id, type=EdgeType.CONTAINS),
                GraphEdge(source=kernel_mod.id, target=small.id, type=EdgeType.CONTAINS),
                GraphEdge(source=big.id, target=small.id, type=EdgeType.CALLS),
                GraphEdge(source=big.id, target=kernel_mod.id, type=EdgeType.DEPENDS_ON),
            ],
        )
        KnowledgeStorage(project_root).save_graph(graph)
        # Both files exist on disk with small content (line_count from node is used first).
        _write(project_root, "vivify/kernel/big.py", lines=10)
        _write(project_root, "vivify/kernel/small.py", lines=10)

        provider = KnowledgeContextProvider(project_root)
        result = provider.recommend_files(
            "kernel feature", project_root, max_files=5,
        )
        names = [p.name for p in result]
        assert "big.py" not in names
        assert names == ["small.py"]

    def test_respects_max_files_limit(self, project_root: Path):
        kernel_mod = _module_node(
            "vivify/kernel", summary="kernel feature pipeline", tags=["kernel"],
        )
        files = [
            _file_node(f"vivify/kernel/f{i}.py") for i in range(5)
        ]
        edges = [
            GraphEdge(source=kernel_mod.id, target=f.id, type=EdgeType.CONTAINS)
            for f in files
        ]
        graph = KnowledgeGraph(
            metadata=GraphMetadata(),
            nodes=[kernel_mod, *files],
            edges=edges,
        )
        KnowledgeStorage(project_root).save_graph(graph)
        for f in files:
            _write(project_root, f.path)

        provider = KnowledgeContextProvider(project_root)
        result = provider.recommend_files(
            "kernel feature pipeline", project_root, max_files=2,
        )
        assert len(result) == 2
        result_all = provider.recommend_files(
            "kernel feature pipeline", project_root, max_files=10,
        )
        assert len(result_all) == 5

    def test_relevance_matching(self, project_root: Path):
        """Files under matched modules are returned; files under unrelated ones aren't."""
        kernel_mod = _module_node(
            "vivify/kernel",
            summary="feature pipeline orchestrator",
            tags=["feature", "pipeline", "kernel"],
        )
        unrelated_mod = _module_node(
            "vivify/zzz_unrelated", summary="xyzzy plover", tags=["xyzzy"],
        )
        kernel_file = _file_node("vivify/kernel/loop.py")
        other_file = _file_node("vivify/zzz_unrelated/quux.py")
        graph = KnowledgeGraph(
            metadata=GraphMetadata(),
            nodes=[kernel_mod, unrelated_mod, kernel_file, other_file],
            edges=[
                GraphEdge(source=kernel_mod.id, target=kernel_file.id, type=EdgeType.CONTAINS),
                GraphEdge(source=unrelated_mod.id, target=other_file.id, type=EdgeType.CONTAINS),
            ],
        )
        KnowledgeStorage(project_root).save_graph(graph)
        _write(project_root, "vivify/kernel/loop.py")
        _write(project_root, "vivify/zzz_unrelated/quux.py")

        provider = KnowledgeContextProvider(project_root)
        result = provider.recommend_files(
            "feature pipeline kernel", project_root, max_files=5,
        )
        names = [p.name for p in result]
        assert "loop.py" in names
        assert "quux.py" not in names
