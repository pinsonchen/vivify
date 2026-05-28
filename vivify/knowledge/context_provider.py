"""Knowledge context provider: selects and formats relevant knowledge for prompts.

Three-level injection strategy:
- L1 (always, ~500 tokens): Project overview + module list with one-line descriptions
- L2 (relevance-based, ~1500 tokens): Detailed info for top-3 related modules
- L3 (on-demand, optional): Specific API signatures when feature targets known files

Token budget: L1 + L2 + conventions ≈ 2000-2500 tokens total
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from vivify.knowledge.models import (
    CodeConvention,
    EdgeType,
    GraphNode,
    KnowledgeGraph,
    NodeType,
)
from vivify.knowledge.storage import KnowledgeStorage

logger = logging.getLogger(__name__)


class KnowledgeContextProvider:
    """Provides relevant knowledge context for AI prompts.

    The provider lazily loads the knowledge graph from
    ``.vivify/knowledge/`` and formats per-prompt context blocks based
    on the feature's title/description. Loading is cached to avoid
    re-reading from disk on every prompt.
    """

    def __init__(self, project_root: Path):
        self.root = Path(project_root)
        self.storage = KnowledgeStorage(self.root)
        self._graph: Optional[KnowledgeGraph] = None
        self._graph_loaded = False
        self._conventions: Optional[List[CodeConvention]] = None

    # ── Public API ──────────────────────────────────────────────────────────

    def get_targeted_context(
        self, feature_title: str, feature_description: str = ""
    ) -> str:
        """精准匹配：模块 → 文件 → 函数/类，逐层定位.

        算法：
        1. 从 feature 描述中提取关键实体名（函数名、类名、文件名）
        2. 在知识图谱中搜索这些实体节点（type=FUNCTION/CLASS/FILE）
        3. 若找到匹配节点，注入节点所在模块上下文、签名/描述、edge 关系
        4. 若未找到，fallback 到 get_context_for_feature()

        Returns:
            格式化的知识上下文字符串
        """
        try:
            graph = self._get_graph()
            if graph is None or not graph.nodes:
                return ""

            combined = f"{feature_title} {feature_description}"
            entities = self._extract_entities(combined)
            if not entities:
                return ""

            # Search for entity nodes in the graph
            matched_nodes: List[GraphNode] = []
            for node in graph.nodes:
                if node.type not in (NodeType.FUNCTION, NodeType.CLASS, NodeType.FILE):
                    continue
                node_name_lower = node.name.lower()
                for entity in entities:
                    if entity.lower() == node_name_lower:
                        matched_nodes.append(node)
                        break
                    # Also check path for file matches
                    if node.type == NodeType.FILE and entity.lower() in node.path.lower():
                        matched_nodes.append(node)
                        break

            if not matched_nodes:
                return ""

            # Build targeted context
            parts: List[str] = []
            seen_modules: Set[str] = set()
            for node in matched_nodes[:5]:  # Limit to top 5 matches
                node_block = self._format_entity_context(node, graph, seen_modules)
                if node_block:
                    parts.append(node_block)

            if not parts:
                return ""

            context = "\n\n".join(parts)
            return f"## 精准知识上下文\n\n{context}"
        except Exception:
            logger.debug("get_targeted_context failed", exc_info=True)
            return ""

    def get_historical_context(
        self, category: str, title: str, storage
    ) -> str:
        """搜索 knowledge_entries 中的历史解决方案.

        Args:
            category: Issue category
            title: Issue title (用于模糊搜索)
            storage: StorageProvider 实例

        Returns:
            格式化的历史经验块（用于 prompt prepend）
        """
        try:
            if storage is None:
                return ""
            entries = storage.search_knowledge(category, title[:50], limit=3)
            if not entries:
                return ""
            # Filter success=True entries
            successful = [e for e in entries if e.success]
            if not successful:
                return ""

            lines = ["## Previously Solved Similar Issues"]
            for i, entry in enumerate(successful[:3], 1):
                pattern = (entry.pattern or "")[:100]
                summary = (entry.solution_summary or "")[:200]
                lines.append(f"{i}. {pattern}: {summary}")

            return "\n".join(lines)
        except Exception:
            logger.debug("get_historical_context failed", exc_info=True)
            return ""

    def get_conventions_for_system_prompt(self) -> str:
        """将项目约定格式化为适合 --append-system-prompt 的文本.

        从知识图谱的 conventions 中提取命名规范、代码风格、架构模式、测试要求。
        格式化为简洁的指导文本（<500字符），适合作为 system prompt 后缀。
        若无 conventions 数据，返回空字符串。
        """
        try:
            conventions = self._get_conventions()
            if not conventions:
                return ""

            # Group by category for compact output
            categorized: Dict[str, List[str]] = {}
            for conv in conventions:
                cat = conv.category or "general"
                categorized.setdefault(cat, []).append(conv.rule)

            lines: List[str] = ["[Project Conventions]"]
            char_count = len(lines[0])
            for cat, rules in categorized.items():
                for rule in rules:
                    line = f"- [{cat}] {rule}"
                    if char_count + len(line) + 1 > 480:
                        break
                    lines.append(line)
                    char_count += len(line) + 1
                if char_count >= 480:
                    break

            if len(lines) <= 1:
                return ""
            return "\n".join(lines)
        except Exception:
            logger.debug("get_conventions_for_system_prompt failed", exc_info=True)
            return ""

    def get_context_for_feature(
        self,
        feature_title: str,
        feature_description: str = "",
        max_tokens: int = 2500,
    ) -> str:
        """Get knowledge context block formatted for prompt injection.

        Args:
            feature_title: Feature title for relevance matching.
            feature_description: Feature description for relevance matching.
            max_tokens: Approximate token budget (chars / 4 as rough estimate).

        Returns:
            Formatted markdown context block, or empty string if no
            knowledge available.
        """
        graph = self._get_graph()
        if graph is None or not graph.nodes:
            return ""

        parts: List[str] = []
        # Rough char budget (~4 chars per token); keep a soft floor.
        remaining_budget = max(max_tokens, 0) * 4

        # L1: Project overview + module list (always included).
        l1 = self._build_l1_overview(graph)
        if l1:
            parts.append(l1)
            remaining_budget -= len(l1)

        # L2: Related modules detail (top-3 by relevance).
        if remaining_budget > 500:
            l2 = self._build_l2_related_modules(
                graph,
                feature_title,
                feature_description,
                max_chars=min(remaining_budget - 500, 6000),
            )
            if l2:
                parts.append(l2)
                remaining_budget -= len(l2)

        # Conventions (always included, compact).
        conventions = self._get_conventions()
        if conventions and remaining_budget > 200:
            conv_block = self._build_conventions_block(
                conventions,
                max_chars=min(remaining_budget, 800),
            )
            if conv_block:
                parts.append(conv_block)

        context = "\n\n".join(p for p in parts if p)
        if not context:
            return ""

        return f"## 项目知识上下文\n\n{context}"

    def get_context_for_goal(
        self,
        goal_name: str,
        goal_description: str = "",
    ) -> str:
        """Get knowledge context for goal decomposition (lighter than feature)."""
        graph = self._get_graph()
        if graph is None or not graph.nodes:
            return ""
        return self._build_l1_overview(graph)

    # ── L1: Project Overview ────────────────────────────────────────────────

    def _build_l1_overview(self, graph: KnowledgeGraph) -> str:
        """Build L1 project overview (~500 tokens)."""
        meta = graph.metadata
        modules = graph.get_module_nodes()

        lines: List[str] = []
        if meta.project_name or meta.description:
            name = meta.project_name or "(unnamed)"
            if meta.description:
                lines.append(f"**项目**: {name} — {meta.description}")
            else:
                lines.append(f"**项目**: {name}")

        if meta.languages:
            lines.append(f"**技术栈**: {', '.join(meta.languages)}")

        if meta.frameworks:
            lines.append(f"**框架**: {', '.join(meta.frameworks)}")

        if modules:
            lines.append("\n**模块架构**:")
            for m in sorted(modules, key=lambda n: n.path):
                summary = m.summary or m.responsibility or m.name
                summary = summary.split("\n")[0][:80]
                lines.append(f"- `{m.path}/` — {summary}")

        if graph.layers:
            lines.append("\n**架构层级**:")
            for layer in graph.layers:
                desc = (layer.description or "")[:60]
                lines.append(f"- {layer.name}: {desc}")

        return "\n".join(lines)

    # ── L2: Related Modules ─────────────────────────────────────────────────

    def _build_l2_related_modules(
        self,
        graph: KnowledgeGraph,
        title: str,
        description: str,
        max_chars: int = 6000,
    ) -> str:
        """Build L2 related module details (~1500 tokens)."""
        modules = graph.get_module_nodes()
        if not modules:
            return ""

        query_tokens = self._tokenize(f"{title} {description}")
        if not query_tokens:
            return ""

        scored = []
        for m in modules:
            score = self._compute_relevance(m, query_tokens)
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_modules = [m for score, m in scored[:3] if score > 0]
        if not top_modules:
            return ""

        details: List[str] = []
        char_count = 0
        for m in top_modules:
            detail = self._format_module_detail(m, graph)
            if char_count + len(detail) > max_chars:
                break
            details.append(detail)
            char_count += len(detail)

        if not details:
            return ""
        return "**相关模块详情**:\n\n" + "\n\n".join(details)

    def _compute_relevance(
        self,
        module: GraphNode,
        query_tokens: Set[str],
    ) -> float:
        """Compute relevance score between query tokens and module attributes."""
        if not query_tokens:
            return 0.0
        module_tokens = self._tokenize(
            f"{module.name} {module.summary} {module.responsibility} "
            f"{' '.join(module.exports)} {' '.join(module.tags)} "
            f"{module.path}"
        )
        if not module_tokens:
            return 0.0
        intersection = query_tokens & module_tokens
        return len(intersection) / (len(query_tokens) + 0.1)

    def _tokenize(self, text: str) -> Set[str]:
        """Tokenize: split on non-alphanumeric, lowercase, expand camel/snake."""
        if not text:
            return set()
        # Match latin words, digits, and CJK unified ideographs.
        raw_tokens = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", text)
        expanded: Set[str] = set()
        for tok in raw_tokens:
            lowered = tok.lower()
            if len(lowered) >= 2:
                expanded.add(lowered)
            # Split snake_case / digit boundaries.
            for piece in re.split(r"_+|(\d+)", lowered):
                if piece and len(piece) > 2:
                    expanded.add(piece)
            # Split camelCase / PascalCase using the original token.
            camel_parts = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+", tok)
            for piece in camel_parts:
                p = piece.lower()
                if len(p) > 2:
                    expanded.add(p)
            # CJK: also keep single ideographs of length >= 2 already covered;
            # split CJK substrings into 2-char shingles for fuzzier matching.
            cjk = re.findall(r"[\u4e00-\u9fff]+", tok)
            for run in cjk:
                if len(run) >= 2:
                    for i in range(len(run) - 1):
                        expanded.add(run[i : i + 2])
        return {t for t in expanded if t}

    def _format_module_detail(
        self,
        module: GraphNode,
        graph: KnowledgeGraph,
    ) -> str:
        """Format a single module's detail for L2 injection."""
        lines = [f"### {module.name} (`{module.path}/`)"]
        if module.responsibility:
            lines.append(module.responsibility)
        elif module.summary:
            lines.append(module.summary)
        if module.exports:
            exports_str = ", ".join(module.exports[:15])
            lines.append(f"**导出**: {exports_str}")
        if module.dependencies:
            deps_str = ", ".join(module.dependencies[:10])
            lines.append(f"**依赖**: {deps_str}")
        if module.tags:
            tags_str = ", ".join(module.tags[:8])
            lines.append(f"**标签**: {tags_str}")
        return "\n".join(lines)

    # ── Conventions ─────────────────────────────────────────────────────────

    def _build_conventions_block(
        self,
        conventions: List[CodeConvention],
        max_chars: int = 800,
    ) -> str:
        """Format code conventions for injection."""
        if not conventions:
            return ""
        lines = ["**代码规范**:"]
        char_count = len(lines[0])
        for conv in conventions:
            line = f"- [{conv.category}] {conv.rule}"
            if char_count + len(line) > max_chars:
                break
            lines.append(line)
            char_count += len(line) + 1
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    # ── File Recommendation ─────────────────────────────────────────────────

    def recommend_files(
        self,
        feature_description: str,
        workspace: Path,
        max_files: int = 3,
    ) -> List[Path]:
        """Recommend core files related to a feature for ``--attachment``.

        Algorithm:
        1. Tokenize ``feature_description`` with :meth:`_tokenize`.
        2. Score every module via :meth:`_compute_relevance` (Jaccard).
        3. Keep the top relevant modules (score > 0).
        4. Collect file nodes contained by those modules.
        5. Rank files by edge degree (in + out); more connected = more core.
        6. Filter: file exists on disk, not under a test dir / ``test_`` prefix,
           and fewer than 500 lines (to avoid wasting tokens).

        Args:
            feature_description: Free-form feature text used for matching.
            workspace: Project root used to resolve relative paths.
            max_files: Maximum number of files to return.

        Returns:
            Absolute paths of recommended files (up to ``max_files``).
        """
        graph = self._get_graph()
        if graph is None or not graph.nodes:
            return []

        query_tokens = self._tokenize(feature_description)
        if not query_tokens:
            return []

        modules = graph.get_module_nodes()
        if not modules:
            return []

        scored = [
            (self._compute_relevance(m, query_tokens), m) for m in modules
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        top_modules = [m for score, m in scored[:3] if score > 0]
        if not top_modules:
            return []

        top_module_ids = {m.id for m in top_modules}
        top_module_paths = {m.path.replace("\\", "/") for m in top_modules}

        # Collect candidate file ids via CONTAINS edges from top modules.
        file_ids: Set[str] = set()
        for edge in graph.edges:
            if edge.type == EdgeType.CONTAINS and edge.source in top_module_ids:
                file_ids.add(edge.target)

        # Fallback: also include file nodes whose path lives under a top module.
        for node in graph.nodes:
            if node.type != NodeType.FILE:
                continue
            node_path = node.path.replace("\\", "/")
            for mod_path in top_module_paths:
                if not mod_path:
                    continue
                if node_path == mod_path or node_path.startswith(mod_path + "/"):
                    file_ids.add(node.id)
                    break

        if not file_ids:
            return []

        # Compute edge degree (in + out) for each candidate.
        degree: Dict[str, int] = {fid: 0 for fid in file_ids}
        for edge in graph.edges:
            if edge.source in degree:
                degree[edge.source] += 1
            if edge.target in degree:
                degree[edge.target] += 1

        # Higher degree first; ties broken by id for determinism.
        sorted_ids = sorted(
            file_ids, key=lambda fid: (-degree.get(fid, 0), fid)
        )

        workspace = Path(workspace)
        recommended: List[Path] = []
        seen: Set[Path] = set()
        for fid in sorted_ids:
            if len(recommended) >= max_files:
                break
            node = graph.get_node(fid)
            if node is None or node.type != NodeType.FILE:
                continue
            rel_path = (node.path or "").replace("\\", "/")
            if not rel_path:
                continue
            parts = rel_path.split("/")
            if any(p in ("test", "tests") for p in parts):
                continue
            if parts[-1].startswith("test_"):
                continue
            abs_path = workspace / rel_path
            if not abs_path.exists() or not abs_path.is_file():
                continue
            try:
                line_count = node.line_count
                if not line_count:
                    with abs_path.open("r", encoding="utf-8", errors="ignore") as f:
                        line_count = sum(1 for _ in f)
            except Exception:  # pragma: no cover - defensive
                continue
            if line_count >= 500:
                continue
            resolved = abs_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            recommended.append(resolved)
        return recommended

    # ── Entity Extraction & Formatting ─────────────────────────────────────

    def _extract_entities(self, text: str) -> List[str]:
        """从文本中提取可能的代码实体名（函数名、类名、文件名）."""
        entities: List[str] = []
        # Match file patterns: word.py, word.js, etc.
        file_patterns = re.findall(r"[\w/]+\.(?:py|js|ts|go|rs|java|rb)", text)
        entities.extend(file_patterns)
        # Match snake_case identifiers (likely function names)
        snake_patterns = re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", text)
        entities.extend(snake_patterns)
        # Match CamelCase/PascalCase identifiers (likely class names)
        camel_patterns = re.findall(r"\b[A-Z][a-zA-Z0-9]*(?:[A-Z][a-z][a-zA-Z0-9]*)+\b", text)
        entities.extend(camel_patterns)
        # Deduplicate while preserving order
        seen: Set[str] = set()
        unique: List[str] = []
        for e in entities:
            if e not in seen:
                seen.add(e)
                unique.append(e)
        return unique[:10]  # Limit to 10 entities

    def _format_entity_context(
        self, node: GraphNode, graph: KnowledgeGraph, seen_modules: Set[str]
    ) -> str:
        """Format context for a matched entity node."""
        lines: List[str] = []
        # Node info
        type_label = node.type.value.capitalize()
        lines.append(f"**{type_label}**: `{node.name}` (`{node.path}`)"
        )
        if node.summary:
            lines.append(f"  {node.summary[:150]}")

        # Find containing module
        containing_module: Optional[GraphNode] = None
        for edge in graph.edges:
            if edge.target == node.id and edge.type == EdgeType.CONTAINS:
                mod = graph.get_node(edge.source)
                if mod and mod.type == NodeType.MODULE:
                    containing_module = mod
                    break

        if containing_module and containing_module.id not in seen_modules:
            seen_modules.add(containing_module.id)
            mod_info = containing_module.responsibility or containing_module.summary
            if mod_info:
                lines.append(f"  模块 `{containing_module.name}`: {mod_info[:100]}")

        # Edge relationships
        edges = graph.get_edges_for(node.id)
        if edges:
            relations: List[str] = []
            for edge in edges[:5]:  # Limit to 5 edges
                other_id = edge.target if edge.source == node.id else edge.source
                other = graph.get_node(other_id)
                if other:
                    rel_type = edge.type.value
                    direction = "→" if edge.source == node.id else "←"
                    relations.append(f"{direction}{rel_type}:`{other.name}`")
            if relations:
                lines.append(f"  关系: {', '.join(relations)}")

        return "\n".join(lines)

    # ── Cache ───────────────────────────────────────────────────────────────

    def _get_graph(self) -> Optional[KnowledgeGraph]:
        if not self._graph_loaded:
            try:
                self._graph = self.storage.load_graph()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Failed to load knowledge graph", exc_info=True)
                self._graph = None
            self._graph_loaded = True
        return self._graph

    def _get_conventions(self) -> List[CodeConvention]:
        if self._conventions is None:
            try:
                self._conventions = self.storage.load_conventions()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Failed to load conventions", exc_info=True)
                self._conventions = []
        return self._conventions


def get_knowledge_context(
    project_root: Path,
    feature_title: str,
    feature_description: str = "",
) -> str:
    """Convenience function for getting knowledge context.

    Always exception-safe: any failure returns an empty string so callers
    can use this as a non-fatal prompt augmentation.
    """
    try:
        provider = KnowledgeContextProvider(project_root)
        return provider.get_context_for_feature(feature_title, feature_description)
    except Exception:
        logger.debug("get_knowledge_context failed", exc_info=True)
        return ""


__all__ = ["KnowledgeContextProvider", "get_knowledge_context"]
