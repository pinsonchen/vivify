"""Knowledge graph builder: orchestrates the full build pipeline.

Three-phase pipeline:
1. build_structural() — Static analysis (deterministic, zero LLM cost)
2. enrich_semantic() — LLM/wiki semantic enrichment
3. extract_conventions() — Code convention extraction from samples

Supports full rebuild and incremental update modes.
"""

from pathlib import Path
from typing import Optional, List, Dict, Set
import re
import subprocess
import time
import logging
from datetime import datetime, timezone

from vivify.knowledge.models import (
    KnowledgeGraph,
    GraphNode,
    GraphEdge,
    GraphMetadata,
    ArchitectureLayer,
    CodeConvention,
    NodeType,
    EdgeType,
    Complexity,
)
from vivify.knowledge.storage import KnowledgeStorage
from vivify.knowledge.analyzers.static_analyzer import (
    StaticAnalyzer,
    StructuralGraph,
    ModuleInfo,
    FileInfo,
)
from vivify.knowledge.analyzers.semantic_analyzer import SemanticAnalyzer, ModuleSemantics

logger = logging.getLogger(__name__)


# Layer descriptions for architecture layer derivation
_LAYER_DESCRIPTIONS: Dict[str, str] = {
    "api": "API and interface layer - handles external communication",
    "core": "Core business logic and domain models",
    "data": "Data access, storage, and persistence",
    "util": "Shared utilities and helper functions",
    "config": "Configuration, settings, and schema definitions",
    "test": "Testing infrastructure and test suites",
}


class KnowledgeBuilder:
    """Builds and maintains the project knowledge graph."""

    def __init__(
        self,
        project_root: Path,
        qodercli_binary: str = "qodercli",
        wiki_path: str = "",
        permission_mode: str = "bypass_permissions",
        timeout: int = 120,
    ):
        self.root = Path(project_root).resolve()
        self.storage = KnowledgeStorage(self.root)
        self.static_analyzer = StaticAnalyzer(self.root)
        self.semantic_analyzer = SemanticAnalyzer(
            self.root,
            qodercli_binary=qodercli_binary,
            wiki_path=wiki_path,
            permission_mode=permission_mode,
        )
        self.timeout = timeout
        self.wiki_path = wiki_path

    def build_full(self) -> KnowledgeGraph:
        """Full knowledge graph build from scratch.

        Pipeline:
        1. Static analysis → structural graph
        2. Convert to KnowledgeGraph nodes/edges
        3. Semantic enrichment → descriptions, layers
        4. Convention extraction
        5. Persist to .vivify/knowledge/

        Returns the built graph. Timeout-safe: returns partial on timeout.
        """
        start = time.time()
        graph = KnowledgeGraph()

        # Phase 1: Structural analysis
        try:
            structural = self.static_analyzer.analyze()
        except Exception as e:
            logger.error("Structural analysis failed: %s", e)
            return graph

        graph = self._structural_to_graph(structural)

        if time.time() - start > self.timeout:
            logger.warning("Knowledge build timeout after structural phase")
            self._persist(graph)
            return graph

        # Phase 2: Semantic enrichment
        try:
            graph = self._enrich_with_semantics(graph, structural)
        except Exception as e:
            logger.warning("Semantic enrichment failed (degraded): %s", e)

        if time.time() - start > self.timeout:
            logger.warning("Knowledge build timeout after semantic phase")
            self._persist(graph)
            return graph

        # Phase 3: Convention extraction
        try:
            conventions = self._extract_conventions(structural)
            graph.conventions = conventions
        except Exception as e:
            logger.warning("Convention extraction failed (degraded): %s", e)

        # Phase 4: Build architecture layers
        graph.layers = self._derive_layers(graph)

        # Phase 5: Fill metadata
        graph.metadata = self._build_metadata(structural)

        # Persist
        self._persist(graph)

        elapsed = time.time() - start
        logger.info(
            "Knowledge graph built in %.1fs: %d nodes, %d edges, %d conventions",
            elapsed,
            len(graph.nodes),
            len(graph.edges),
            len(graph.conventions),
        )
        return graph

    def build_incremental(self) -> Optional[KnowledgeGraph]:
        """Incremental update: only re-analyze changed files.

        Steps:
        1. Load existing graph and meta
        2. Get current git hash, compare with stored
        3. git diff to find changed files
        4. Re-analyze affected modules only
        5. Merge updates into existing graph
        6. Persist

        Returns None if no update needed.
        """
        # Load existing graph
        existing = self.storage.load_graph()
        if existing is None:
            return self.build_full()

        # Check if update needed
        current_hash = self._get_current_git_hash()
        if not self.storage.needs_update(current_hash):
            return None

        # Get changed files
        last_hash = self.storage.get_last_commit_hash()
        changed_files = self._get_changed_files(last_hash, current_hash)
        if not changed_files:
            # Update hash but no file changes (possibly merge commit)
            existing.metadata.git_commit_hash = current_hash
            self._persist(existing)
            return None

        # Incremental analysis
        try:
            structural = self.static_analyzer.analyze_incremental(changed_files)
        except Exception as e:
            logger.error("Incremental analysis failed: %s", e)
            return None

        # Merge into existing graph
        updated = self._merge_incremental(existing, structural, changed_files)

        # Incremental semantic update (only changed modules)
        try:
            updated = self._incremental_semantic_update(updated, structural)
        except Exception as e:
            logger.warning("Incremental semantic update failed: %s", e)

        # Update metadata
        now = datetime.now(timezone.utc).isoformat()
        updated.metadata.git_commit_hash = current_hash
        updated.metadata.generated_at = now
        # Merge file fingerprints
        updated.metadata.file_fingerprints.update(structural.file_fingerprints)

        # Re-derive layers
        updated.layers = self._derive_layers(updated)

        self._persist(updated)
        return updated

    # --- Conversion Methods ---

    def _structural_to_graph(self, structural: StructuralGraph) -> KnowledgeGraph:
        """Convert StaticAnalyzer output to KnowledgeGraph model.

        Creates:
        - MODULE nodes from ModuleInfo
        - FILE nodes from FileInfo
        - CLASS nodes from FileInfo.classes (only public classes)
        - FUNCTION nodes only for module-level public functions
        - CONTAINS edges (module->file, file->class)
        - DEPENDS_ON edges from inter-module edges
        """
        graph = KnowledgeGraph()
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []

        for mod in structural.modules:
            # Create MODULE node
            mod_id = f"module:{mod.path}"
            mod_exports = self._collect_module_exports(mod)
            mod_deps = [f"module:{target}" for _, target, _ in structural.edges if _ == mod.name]
            # Re-derive deps using the edges that reference this module as source
            mod_deps_ids = []
            for src, tgt, _ in structural.edges:
                if src == mod.name:
                    # Find the target module's path
                    target_mod = next((m for m in structural.modules if m.name == tgt), None)
                    if target_mod:
                        mod_deps_ids.append(f"module:{target_mod.path}")

            mod_node = GraphNode(
                id=mod_id,
                type=NodeType.MODULE,
                name=mod.name,
                path=mod.path,
                complexity=Complexity(mod.complexity),
                exports=mod_exports,
                dependencies=mod_deps_ids,
                line_count=mod.total_lines,
            )
            nodes.append(mod_node)

            # Create FILE nodes and edges
            for fi in mod.files:
                file_id = f"file:{fi.path}"
                file_node = GraphNode(
                    id=file_id,
                    type=NodeType.FILE,
                    name=Path(fi.path).name,
                    path=fi.path,
                    line_count=fi.line_count,
                    functions=fi.functions,
                    classes=fi.classes,
                    exports=fi.exports,
                )
                nodes.append(file_node)

                # CONTAINS edge: module -> file
                edges.append(
                    GraphEdge(
                        source=mod_id,
                        target=file_id,
                        type=EdgeType.CONTAINS,
                        weight=1.0,
                    )
                )

                # Create CLASS nodes (only public classes)
                for cls_name in fi.classes:
                    if not cls_name.startswith("_"):
                        cls_id = f"class:{fi.path}:{cls_name}"
                        cls_node = GraphNode(
                            id=cls_id,
                            type=NodeType.CLASS,
                            name=cls_name,
                            path=fi.path,
                        )
                        nodes.append(cls_node)
                        edges.append(
                            GraphEdge(
                                source=file_id,
                                target=cls_id,
                                type=EdgeType.CONTAINS,
                                weight=1.0,
                            )
                        )

        # Create inter-module DEPENDS_ON edges
        for src_name, tgt_name, edge_type in structural.edges:
            src_mod = next((m for m in structural.modules if m.name == src_name), None)
            tgt_mod = next((m for m in structural.modules if m.name == tgt_name), None)
            if src_mod and tgt_mod:
                src_id = f"module:{src_mod.path}"
                tgt_id = f"module:{tgt_mod.path}"
                kg_edge_type = EdgeType.IMPORTS if edge_type == "imports" else EdgeType.DEPENDS_ON
                edges.append(
                    GraphEdge(
                        source=src_id,
                        target=tgt_id,
                        type=kg_edge_type,
                        weight=0.8,
                    )
                )

        graph.nodes = nodes
        graph.edges = edges
        return graph

    def _enrich_with_semantics(
        self, graph: KnowledgeGraph, structural: StructuralGraph
    ) -> KnowledgeGraph:
        """Add semantic information from LLM/wiki to module nodes."""
        modules_data = []
        for node in graph.get_module_nodes():
            modules_data.append(
                {
                    "name": node.name,
                    "path": node.path,
                    "exports": node.exports,
                    "dependencies": node.dependencies,
                    "total_lines": node.line_count,
                    "complexity": node.complexity.value,
                }
            )

        if not modules_data:
            return graph

        semantics = self.semantic_analyzer.analyze(modules_data)

        # Write semantic info back to graph nodes
        for sem in semantics:
            node = graph.get_node(f"module:{self._find_module_path(sem.name, structural)}")
            if node:
                node.summary = sem.description
                node.responsibility = sem.responsibility
                node.layer = sem.layer
                node.tags = sem.tags

        return graph

    def _extract_conventions(self, structural: StructuralGraph) -> List[CodeConvention]:
        """Extract code conventions from code samples.

        Heuristic approach (no LLM):
        - Naming: snake_case vs camelCase detection
        - Imports: absolute vs relative, grouping style
        - Docstrings: presence ratio, format (Google/Numpy/RST)
        - File organization: test location, config patterns
        """
        conventions: List[CodeConvention] = []

        # Collect stats across all files
        all_functions: List[str] = []
        all_classes: List[str] = []
        has_docstrings = 0
        total_python_files = 0
        relative_imports = 0
        absolute_imports = 0

        for mod in structural.modules:
            for fi in mod.files:
                if fi.language == "python":
                    total_python_files += 1
                    all_functions.extend(fi.functions)
                    all_classes.extend(fi.classes)

                    # Count import styles
                    for imp in fi.imports:
                        if imp.startswith("."):
                            relative_imports += 1
                        else:
                            absolute_imports += 1

        # Check for docstrings by reading a sample of files
        docstring_files = self._count_docstring_files(structural)

        # Naming convention detection
        if all_functions:
            snake_count = sum(1 for f in all_functions if self._is_snake_case(f))
            snake_ratio = snake_count / len(all_functions)
            if snake_ratio > 0.8:
                conventions.append(
                    CodeConvention(
                        category="naming",
                        rule="Use snake_case for function and method names",
                        example="def my_function():",
                        language="python",
                    )
                )
            elif snake_ratio < 0.3:
                conventions.append(
                    CodeConvention(
                        category="naming",
                        rule="Use camelCase for function names",
                        example="function myFunction() {}",
                        language="javascript",
                    )
                )

        if all_classes:
            pascal_count = sum(1 for c in all_classes if self._is_pascal_case(c))
            if pascal_count / len(all_classes) > 0.8:
                conventions.append(
                    CodeConvention(
                        category="naming",
                        rule="Use PascalCase for class names",
                        example="class MyClass:",
                        language="python",
                    )
                )

        # Import style
        total_imports = relative_imports + absolute_imports
        if total_imports > 0:
            if absolute_imports / total_imports > 0.7:
                conventions.append(
                    CodeConvention(
                        category="imports",
                        rule="Prefer absolute imports over relative imports",
                        example="from vivify.kernel.loop import KernelLoop",
                        language="python",
                    )
                )
            elif relative_imports / total_imports > 0.5:
                conventions.append(
                    CodeConvention(
                        category="imports",
                        rule="Use relative imports for intra-package references",
                        example="from .loop import KernelLoop",
                        language="python",
                    )
                )

        # Docstring presence
        if total_python_files > 0 and docstring_files > 0:
            docstring_ratio = docstring_files / total_python_files
            if docstring_ratio > 0.6:
                conventions.append(
                    CodeConvention(
                        category="documentation",
                        rule="Include module-level docstrings in Python files",
                        example='"""Module description."""',
                        language="python",
                    )
                )

        # Test file location pattern
        test_patterns = self._detect_test_patterns(structural)
        if test_patterns:
            conventions.append(
                CodeConvention(
                    category="testing",
                    rule=test_patterns,
                    example="tests/unit/test_<module>.py",
                    language="python",
                )
            )

        return conventions

    def _derive_layers(self, graph: KnowledgeGraph) -> List[ArchitectureLayer]:
        """Group modules into architecture layers based on their semantic layer tag."""
        layer_map: Dict[str, List[str]] = {}
        for node in graph.get_module_nodes():
            layer = node.layer or "other"
            if layer not in layer_map:
                layer_map[layer] = []
            layer_map[layer].append(node.id)

        return [
            ArchitectureLayer(
                name=f"{layer.title()} Layer",
                description=_LAYER_DESCRIPTIONS.get(
                    layer, f"Modules in {layer} category"
                ),
                node_ids=node_ids,
            )
            for layer, node_ids in sorted(layer_map.items())
        ]

    def _build_metadata(self, structural: StructuralGraph) -> GraphMetadata:
        """Build graph metadata from structural analysis."""
        # Detect frameworks from imports
        frameworks = self._detect_frameworks(structural)

        return GraphMetadata(
            project_name=self.root.name,
            description=f"Knowledge graph for {self.root.name}",
            languages=structural.languages,
            frameworks=frameworks,
            git_commit_hash=self._get_current_git_hash(),
            generated_at=datetime.now(timezone.utc).isoformat(),
            version="1.0.0",
            file_fingerprints=structural.file_fingerprints,
        )

    def _persist(self, graph: KnowledgeGraph) -> None:
        """Save graph to disk, including module-level splits."""
        self.storage.save_graph(graph)
        self.storage.save_meta(graph.metadata)
        if graph.conventions:
            self.storage.save_conventions(graph.conventions)

        # Per-module detail shards
        for node in graph.get_module_nodes():
            detail = {
                "name": node.name,
                "path": node.path,
                "summary": node.summary,
                "responsibility": node.responsibility,
                "layer": node.layer,
                "exports": node.exports,
                "dependencies": node.dependencies,
                "files": [
                    n.name
                    for n in graph.nodes
                    if n.type == NodeType.FILE
                    and n.path.startswith(node.path + "/")
                ],
            }
            self.storage.save_module_detail(node.name, detail)

    # --- Incremental Helpers ---

    def _merge_incremental(
        self,
        existing: KnowledgeGraph,
        structural: StructuralGraph,
        changed_files: List[str],
    ) -> KnowledgeGraph:
        """Merge incremental analysis results into existing graph.

        1. Determine affected modules from structural analysis
        2. Remove old nodes/edges for affected modules
        3. Add new nodes/edges from fresh analysis
        """
        # Affected module paths
        affected_mod_paths: Set[str] = {mod.path for mod in structural.modules}

        # Remove old nodes/edges for affected modules
        nodes_to_keep: List[GraphNode] = []
        removed_node_ids: Set[str] = set()

        for node in existing.nodes:
            # Check if this node belongs to an affected module
            belongs_to_affected = False
            for mod_path in affected_mod_paths:
                if node.path == mod_path or node.path.startswith(mod_path + "/"):
                    belongs_to_affected = True
                    break
            if belongs_to_affected:
                removed_node_ids.add(node.id)
            else:
                nodes_to_keep.append(node)

        # Remove edges involving removed nodes
        edges_to_keep: List[GraphEdge] = [
            e
            for e in existing.edges
            if e.source not in removed_node_ids and e.target not in removed_node_ids
        ]

        # Build new graph fragment from structural
        new_fragment = self._structural_to_graph(structural)

        # Merge
        existing.nodes = nodes_to_keep + new_fragment.nodes
        existing.edges = edges_to_keep + new_fragment.edges

        return existing

    def _incremental_semantic_update(
        self, graph: KnowledgeGraph, structural: StructuralGraph
    ) -> KnowledgeGraph:
        """Re-run semantic analysis only on changed modules."""
        changed_modules_data = []
        for mod in structural.modules:
            mod_node = graph.get_node(f"module:{mod.path}")
            if mod_node:
                changed_modules_data.append(
                    {
                        "name": mod_node.name,
                        "path": mod_node.path,
                        "exports": mod_node.exports,
                        "dependencies": mod_node.dependencies,
                        "total_lines": mod_node.line_count,
                        "complexity": mod_node.complexity.value,
                    }
                )

        if not changed_modules_data:
            return graph

        # Get existing semantics for context
        existing_semantics = []
        for node in graph.get_module_nodes():
            if node.summary or node.responsibility:
                existing_semantics.append(
                    ModuleSemantics(
                        name=node.name,
                        description=node.summary,
                        responsibility=node.responsibility,
                        layer=node.layer,
                        tags=node.tags,
                    )
                )

        new_semantics = self.semantic_analyzer.analyze_incremental(
            changed_modules_data, existing_semantics
        )

        # Apply updates
        for sem in new_semantics:
            # Find module node by name
            for node in graph.get_module_nodes():
                if node.name == sem.name:
                    node.summary = sem.description
                    node.responsibility = sem.responsibility
                    node.layer = sem.layer
                    node.tags = sem.tags
                    break

        return graph

    # --- Git Helpers ---

    def _get_current_git_hash(self) -> str:
        """Get current HEAD commit hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(self.root),
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def _get_changed_files(self, old_hash: str, new_hash: str) -> List[str]:
        """Get list of changed files between two commits."""
        if not old_hash or not new_hash:
            return []
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", f"{old_hash}..{new_hash}"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.root),
            )
            if result.returncode == 0:
                return [f for f in result.stdout.strip().split("\n") if f]
            return []
        except Exception:
            return []

    # --- Internal Helpers ---

    def _collect_module_exports(self, mod: ModuleInfo) -> List[str]:
        """Collect all public exports from a module's files."""
        exports: List[str] = []
        for fi in mod.files:
            exports.extend(fi.exports)
        # Deduplicate while preserving order
        seen: Set[str] = set()
        unique: List[str] = []
        for e in exports:
            if e not in seen:
                seen.add(e)
                unique.append(e)
        return unique

    def _find_module_path(self, mod_name: str, structural: StructuralGraph) -> str:
        """Find the path for a module by name."""
        for mod in structural.modules:
            if mod.name == mod_name:
                return mod.path
        return mod_name

    def _detect_frameworks(self, structural: StructuralGraph) -> List[str]:
        """Detect frameworks from imports across all modules."""
        framework_indicators: Dict[str, str] = {
            "fastapi": "FastAPI",
            "flask": "Flask",
            "django": "Django",
            "react": "React",
            "express": "Express",
            "next": "Next.js",
            "pytest": "pytest",
            "click": "Click",
            "typer": "Typer",
            "sqlalchemy": "SQLAlchemy",
            "pydantic": "Pydantic",
        }
        found: Set[str] = set()
        for mod in structural.modules:
            for fi in mod.files:
                for imp in fi.imports:
                    imp_lower = imp.split(".")[0].lower()
                    if imp_lower in framework_indicators:
                        found.add(framework_indicators[imp_lower])
        return sorted(found)

    def _is_snake_case(self, name: str) -> bool:
        """Check if a name follows snake_case convention."""
        if name.startswith("_"):
            name = name.lstrip("_")
        if not name:
            return True
        return bool(re.match(r"^[a-z][a-z0-9_]*$", name))

    def _is_pascal_case(self, name: str) -> bool:
        """Check if a name follows PascalCase convention."""
        return bool(re.match(r"^[A-Z][a-zA-Z0-9]*$", name))

    def _count_docstring_files(self, structural: StructuralGraph) -> int:
        """Count Python files that contain module-level docstrings."""
        count = 0
        sample_limit = 20  # Only check up to 20 files for performance
        checked = 0

        for mod in structural.modules:
            for fi in mod.files:
                if fi.language != "python":
                    continue
                if checked >= sample_limit:
                    break
                checked += 1
                filepath = self.root / fi.path
                try:
                    content = filepath.read_text(encoding="utf-8", errors="replace")
                    # Check for module-level docstring (first non-comment, non-empty line)
                    stripped = content.lstrip()
                    if stripped.startswith('"""') or stripped.startswith("'''"):
                        count += 1
                    elif stripped.startswith("#") or stripped.startswith("from") or stripped.startswith("import"):
                        # Check after initial comments/encoding declarations
                        lines = content.split("\n")
                        for line in lines[:10]:
                            line_stripped = line.strip()
                            if not line_stripped or line_stripped.startswith("#"):
                                continue
                            if line_stripped.startswith('"""') or line_stripped.startswith("'''"):
                                count += 1
                            break
                except OSError:
                    pass
            if checked >= sample_limit:
                break

        return count

    def _detect_test_patterns(self, structural: StructuralGraph) -> str:
        """Detect test file location patterns."""
        has_tests_dir = False
        has_inline_tests = False

        for mod in structural.modules:
            if "test" in mod.name.lower():
                has_tests_dir = True
            for fi in mod.files:
                if fi.path.startswith("tests/") or "/tests/" in fi.path:
                    has_tests_dir = True
                elif "test_" in Path(fi.path).name and "tests/" not in fi.path:
                    has_inline_tests = True

        if has_tests_dir and not has_inline_tests:
            return "Tests are organized in a separate tests/ directory"
        elif has_inline_tests and not has_tests_dir:
            return "Tests are co-located alongside source files"
        elif has_tests_dir:
            return "Tests are primarily in a separate tests/ directory"
        return ""
