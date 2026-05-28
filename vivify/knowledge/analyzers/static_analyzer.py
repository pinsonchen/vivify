"""Static code analyzer using Python's built-in ast module.

Extracts module boundaries, import relationships, class/function definitions
without any LLM calls. Deterministic: same code always produces same output.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Tuple, Set, Optional
import ast
import fnmatch
import hashlib
import logging
import os
import re

logger = logging.getLogger(__name__)


@dataclass
class FileInfo:
    """Information extracted from a single source file."""

    path: str  # relative path from project root
    language: str  # python, javascript, typescript, etc.
    line_count: int
    imports: List[str]  # imported modules/packages
    exports: List[str]  # exported symbols (function names, class names)
    classes: List[str]
    functions: List[str]
    decorators: List[str]  # file-level decorator patterns
    fingerprint: str  # content hash


@dataclass
class ModuleInfo:
    """Information about a discovered module/package."""

    name: str  # module name (e.g. "kernel", "agents")
    path: str  # relative path
    files: List[FileInfo] = field(default_factory=list)
    total_lines: int = 0
    complexity: str = "simple"  # simple/moderate/complex
    internal_imports: List[str] = field(default_factory=list)
    external_imports: List[str] = field(default_factory=list)


@dataclass
class StructuralGraph:
    """Complete structural analysis result."""

    modules: List[ModuleInfo]
    edges: List[Tuple[str, str, str]]  # (source_module, target_module, edge_type)
    file_fingerprints: Dict[str, str]  # path -> hash
    project_root: str
    languages: List[str]


class StaticAnalyzer:
    """Zero-cost static analysis engine using Python builtins.

    Performs deterministic structural analysis of a codebase:
    - Discovers modules (Python packages, JS/TS workspaces)
    - Extracts imports, classes, functions from each file
    - Derives inter-module dependency edges
    - Computes file fingerprints for incremental analysis
    """

    # Supported file extensions by language
    LANGUAGE_EXTENSIONS: Dict[str, List[str]] = {
        "python": [".py"],
        "javascript": [".js", ".jsx", ".mjs", ".cjs"],
        "typescript": [".ts", ".tsx"],
    }

    def __init__(self, project_root: Path, ignore_patterns: Optional[List[str]] = None):
        self.root = Path(project_root).resolve()
        self.ignore_patterns = ignore_patterns or self._default_ignores()
        self._extra_ignores = self._load_ignore_file()
        self.ignore_patterns.extend(self._extra_ignores)

    def analyze(self) -> StructuralGraph:
        """Full static analysis of the project.

        Returns a StructuralGraph representing all modules, files, and
        inter-module dependency edges.
        """
        module_paths = self._discover_modules()
        modules: List[ModuleInfo] = []
        all_fingerprints: Dict[str, str] = {}
        languages_seen: Set[str] = set()

        for mod_path in module_paths:
            mod_info = self._analyze_module(mod_path)
            if mod_info and mod_info.files:
                mod_info.complexity = self._compute_complexity(mod_info)
                modules.append(mod_info)
                for fi in mod_info.files:
                    all_fingerprints[fi.path] = fi.fingerprint
                    languages_seen.add(fi.language)

        edges = self._derive_edges(modules)

        return StructuralGraph(
            modules=modules,
            edges=edges,
            file_fingerprints=all_fingerprints,
            project_root=str(self.root),
            languages=sorted(languages_seen),
        )

    def analyze_incremental(self, changed_files: List[str]) -> StructuralGraph:
        """Incremental analysis: only re-analyze changed files and their modules.

        Args:
            changed_files: List of relative file paths that have changed.

        Returns:
            A StructuralGraph containing only the affected modules.
        """
        # Determine which modules are affected
        affected_modules: Set[str] = set()
        all_module_paths = self._discover_modules()

        for changed in changed_files:
            changed_path = Path(changed)
            for mod_path in all_module_paths:
                mod_rel = Path(mod_path)
                try:
                    changed_path.relative_to(mod_rel)
                    affected_modules.add(mod_path)
                    break
                except ValueError:
                    continue

        # Re-analyze only affected modules
        modules: List[ModuleInfo] = []
        all_fingerprints: Dict[str, str] = {}
        languages_seen: Set[str] = set()

        for mod_path in affected_modules:
            mod_info = self._analyze_module(mod_path)
            if mod_info and mod_info.files:
                mod_info.complexity = self._compute_complexity(mod_info)
                modules.append(mod_info)
                for fi in mod_info.files:
                    all_fingerprints[fi.path] = fi.fingerprint
                    languages_seen.add(fi.language)

        edges = self._derive_edges(modules)

        return StructuralGraph(
            modules=modules,
            edges=edges,
            file_fingerprints=all_fingerprints,
            project_root=str(self.root),
            languages=sorted(languages_seen),
        )

    # --- Module Discovery ---

    def _discover_modules(self) -> List[str]:
        """Discover top-level modules/packages in the project.

        Python: directories containing __init__.py
        JS/TS: package.json workspaces, or src/ subdirectories
        """
        modules: List[str] = []

        # Check top-level directories for Python packages
        for entry in sorted(os.listdir(self.root)):
            if self._should_ignore(entry):
                continue
            full_path = self.root / entry
            if full_path.is_dir():
                # Python package: has __init__.py
                if (full_path / "__init__.py").exists():
                    modules.append(entry)
                # JS/TS module: has package.json or index.js/ts
                elif any(
                    (full_path / f).exists()
                    for f in ["package.json", "index.js", "index.ts"]
                ):
                    modules.append(entry)
                # Also check src/ convention
                elif entry == "src" and full_path.is_dir():
                    modules.append(entry)

        # Check for monorepo workspaces via root package.json
        root_pkg = self.root / "package.json"
        if root_pkg.exists():
            try:
                import json

                with open(root_pkg, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                workspaces = pkg_data.get("workspaces", [])
                if isinstance(workspaces, dict):
                    workspaces = workspaces.get("packages", [])
                for ws in workspaces:
                    # Resolve glob patterns in workspace definitions
                    ws_path = self.root / ws
                    if ws_path.is_dir() and not self._should_ignore(ws):
                        rel = str(ws_path.relative_to(self.root))
                        if rel not in modules:
                            modules.append(rel)
            except (json.JSONDecodeError, OSError):
                pass

        # Discover sub-packages within found Python packages
        expanded: List[str] = []
        for mod in modules:
            mod_path = self.root / mod
            if (mod_path / "__init__.py").exists():
                # Check for sub-packages
                has_subpackages = False
                for sub in sorted(os.listdir(mod_path)):
                    if self._should_ignore(sub):
                        continue
                    sub_path = mod_path / sub
                    if sub_path.is_dir() and (sub_path / "__init__.py").exists():
                        expanded.append(f"{mod}/{sub}")
                        has_subpackages = True
                if not has_subpackages:
                    expanded.append(mod)
                else:
                    # Also include the parent module for its own files
                    expanded.append(mod)
            else:
                expanded.append(mod)

        return sorted(set(expanded))

    # --- Module Analysis ---

    def _analyze_module(self, mod_path: str) -> Optional[ModuleInfo]:
        """Analyze all files within a module."""
        full_path = self.root / mod_path
        if not full_path.is_dir():
            return None

        name = Path(mod_path).name
        files: List[FileInfo] = []
        total_lines = 0
        internal_imports: Set[str] = set()
        external_imports: Set[str] = set()

        for root_dir, dirs, filenames in os.walk(full_path):
            # Filter out ignored directories in-place
            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in sorted(filenames):
                if self._should_ignore(filename):
                    continue

                filepath = Path(root_dir) / filename
                rel_path = str(filepath.relative_to(self.root))
                language = self._detect_language(filepath)

                if language is None:
                    continue

                file_info = self._analyze_file(filepath, rel_path, language)
                if file_info:
                    files.append(file_info)
                    total_lines += file_info.line_count

                    # Categorize imports
                    for imp in file_info.imports:
                        if self._is_internal_import(imp):
                            internal_imports.add(imp)
                        else:
                            external_imports.add(imp)

        if not files:
            return None

        return ModuleInfo(
            name=name,
            path=mod_path,
            files=files,
            total_lines=total_lines,
            internal_imports=sorted(internal_imports),
            external_imports=sorted(external_imports),
        )

    # --- File Analysis Dispatch ---

    def _analyze_file(
        self, filepath: Path, rel_path: str, language: str
    ) -> Optional[FileInfo]:
        """Dispatch file analysis based on language."""
        try:
            if language == "python":
                return self._analyze_python_file(filepath, rel_path)
            elif language in ("javascript", "typescript"):
                return self._analyze_js_file(filepath, rel_path, language)
        except Exception as e:
            logger.debug(f"Failed to analyze {rel_path}: {e}")
            return None
        return None

    # --- Python Analysis ---

    def _analyze_python_file(self, filepath: Path, rel_path: str) -> Optional[FileInfo]:
        """Parse a Python file using ast module.

        Extracts:
        - Import / ImportFrom statements
        - ClassDef names
        - FunctionDef / AsyncFunctionDef names
        - Decorator names
        """
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.debug(f"Cannot read {rel_path}: {e}")
            return None

        line_count = source.count("\n") + (1 if source and not source.endswith("\n") else 0)
        fingerprint = self._compute_fingerprint_from_content(source)

        imports: List[str] = []
        classes: List[str] = []
        functions: List[str] = []
        decorators: List[str] = []
        exports: List[str] = []

        try:
            tree = ast.parse(source, filename=str(filepath))
        except SyntaxError as e:
            logger.debug(f"Syntax error in {rel_path}: {e}")
            # Return basic info even if parsing fails
            return FileInfo(
                path=rel_path,
                language="python",
                line_count=line_count,
                imports=[],
                exports=[],
                classes=[],
                functions=[],
                decorators=[],
                fingerprint=fingerprint,
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
                # Handle relative imports
                elif node.level > 0:
                    # Relative import without module name (from . import x)
                    for alias in node.names:
                        imports.append(f".{'.' * (node.level - 1)}{alias.name}")

        # Only look at top-level definitions
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                classes.append(node.name)
                exports.append(node.name)
                # Collect class decorators
                for dec in node.decorator_list:
                    dec_name = self._get_decorator_name(dec)
                    if dec_name:
                        decorators.append(dec_name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
                if not node.name.startswith("_"):
                    exports.append(node.name)
                # Collect function decorators
                for dec in node.decorator_list:
                    dec_name = self._get_decorator_name(dec)
                    if dec_name:
                        decorators.append(dec_name)

        # Check __all__ for explicit exports
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            exports = []
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(
                                    elt.value, str
                                ):
                                    exports.append(elt.value)

        return FileInfo(
            path=rel_path,
            language="python",
            line_count=line_count,
            imports=sorted(set(imports)),
            exports=sorted(set(exports)),
            classes=sorted(classes),
            functions=sorted(functions),
            decorators=sorted(set(decorators)),
            fingerprint=fingerprint,
        )

    def _get_decorator_name(self, node: ast.expr) -> Optional[str]:
        """Extract decorator name from AST node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            # e.g. @app.route
            parts = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        elif isinstance(node, ast.Call):
            return self._get_decorator_name(node.func)
        return None

    # --- JS/TS Analysis ---

    # Regex patterns for JS/TS analysis
    _JS_IMPORT_RE = re.compile(
        r"""(?:import\s+(?:(?:[\w*{}\s,]+)\s+from\s+)?['"]([^'"]+)['"]|"""
        r"""require\s*\(\s*['"]([^'"]+)['"]\s*\))""",
        re.MULTILINE,
    )
    _JS_EXPORT_FUNC_RE = re.compile(
        r"""export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)""", re.MULTILINE
    )
    _JS_EXPORT_CLASS_RE = re.compile(
        r"""export\s+(?:default\s+)?class\s+(\w+)""", re.MULTILINE
    )
    _JS_EXPORT_CONST_RE = re.compile(
        r"""export\s+(?:const|let|var)\s+(\w+)""", re.MULTILINE
    )
    _JS_CLASS_RE = re.compile(r"""class\s+(\w+)""", re.MULTILINE)
    _JS_FUNC_RE = re.compile(
        r"""(?:(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)|"""
        r"""(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(?[^)]*\)?\s*=>)""",
        re.MULTILINE,
    )

    def _analyze_js_file(
        self, filepath: Path, rel_path: str, language: str
    ) -> Optional[FileInfo]:
        """Parse a JS/TS file using regex patterns.

        Extracts:
        - import ... from '...' and require() calls
        - exported functions, classes, constants
        - class definitions
        - function definitions
        """
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.debug(f"Cannot read {rel_path}: {e}")
            return None

        line_count = source.count("\n") + (1 if source and not source.endswith("\n") else 0)
        fingerprint = self._compute_fingerprint_from_content(source)

        # Extract imports
        imports: List[str] = []
        for match in self._JS_IMPORT_RE.finditer(source):
            imp = match.group(1) or match.group(2)
            if imp:
                imports.append(imp)

        # Extract classes
        classes = [m.group(1) for m in self._JS_CLASS_RE.finditer(source)]

        # Extract functions
        functions: List[str] = []
        for m in self._JS_FUNC_RE.finditer(source):
            name = m.group(1) or m.group(2)
            if name:
                functions.append(name)

        # Extract exports
        exports: List[str] = []
        exports.extend(m.group(1) for m in self._JS_EXPORT_FUNC_RE.finditer(source))
        exports.extend(m.group(1) for m in self._JS_EXPORT_CLASS_RE.finditer(source))
        exports.extend(m.group(1) for m in self._JS_EXPORT_CONST_RE.finditer(source))

        return FileInfo(
            path=rel_path,
            language=language,
            line_count=line_count,
            imports=sorted(set(imports)),
            exports=sorted(set(exports)),
            classes=sorted(set(classes)),
            functions=sorted(set(functions)),
            decorators=[],  # JS/TS decorators could be added later
            fingerprint=fingerprint,
        )

    # --- Complexity Computation ---

    def _compute_complexity(self, module: ModuleInfo) -> str:
        """Compute module complexity score.

        Based on:
        - Total lines of code
        - Number of files
        - Number of imports
        """
        total_lines = module.total_lines
        file_count = len(module.files)
        import_count = len(module.internal_imports) + len(module.external_imports)

        if total_lines > 2000 or file_count > 20 or import_count > 50:
            return "complex"
        elif total_lines > 500 or file_count > 5 or import_count > 15:
            return "moderate"
        return "simple"

    # --- Edge Derivation ---

    def _derive_edges(self, modules: List[ModuleInfo]) -> List[Tuple[str, str, str]]:
        """Derive inter-module edges from import relationships.

        If module A's files import symbols from module B -> edge(A, B, "imports")
        """
        # Build a map: module_name -> set of top-level package names it provides
        module_packages: Dict[str, Set[str]] = {}
        for mod in modules:
            # A module provides its own name and any parent package paths
            names: Set[str] = set()
            names.add(mod.name)
            # Also map the full dotted path (e.g. "vivify.kernel" -> "kernel")
            parts = mod.path.replace("/", ".").split(".")
            for i in range(len(parts)):
                names.add(".".join(parts[i:]))
            module_packages[mod.name] = names

        # Reverse map: package_prefix -> module_name
        prefix_to_module: Dict[str, str] = {}
        for mod_name, prefixes in module_packages.items():
            for prefix in prefixes:
                prefix_to_module[prefix] = mod_name

        edges: Set[Tuple[str, str, str]] = set()

        for mod in modules:
            for fi in mod.files:
                for imp in fi.imports:
                    # Try to resolve import to a module
                    target = self._resolve_import_to_module(imp, prefix_to_module)
                    if target and target != mod.name:
                        edges.add((mod.name, target, "imports"))

        return sorted(edges)

    def _resolve_import_to_module(
        self, import_path: str, prefix_to_module: Dict[str, str]
    ) -> Optional[str]:
        """Resolve an import path to a module name."""
        # Skip relative imports (they are intra-module)
        if import_path.startswith("."):
            return None

        # Try progressively shorter prefixes
        parts = import_path.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in prefix_to_module:
                return prefix_to_module[prefix]

        # Try last component (module name match)
        for part in parts:
            if part in prefix_to_module:
                return prefix_to_module[part]

        return None

    # --- Fingerprint Computation ---

    def _compute_fingerprint(self, filepath: Path) -> str:
        """Compute file content hash for change detection."""
        try:
            content = filepath.read_bytes()
            return hashlib.sha256(content).hexdigest()[:16]
        except OSError:
            return ""

    def _compute_fingerprint_from_content(self, content: str) -> str:
        """Compute hash from already-read content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    # --- Language Detection ---

    def _detect_language(self, filepath: Path) -> Optional[str]:
        """Detect file language from extension."""
        ext = filepath.suffix.lower()
        for lang, extensions in self.LANGUAGE_EXTENSIONS.items():
            if ext in extensions:
                return lang
        return None

    # --- Internal Import Detection ---

    def _is_internal_import(self, import_path: str) -> bool:
        """Check if an import is internal to the project."""
        if import_path.startswith("."):
            return True
        # Check if import starts with any top-level package in the project
        top_level = import_path.split(".")[0]
        top_level_dir = self.root / top_level
        return (top_level_dir / "__init__.py").exists() or (
            top_level_dir.is_dir()
            and any(
                (top_level_dir / f).exists()
                for f in ["package.json", "index.js", "index.ts"]
            )
        )

    # --- Ignore Patterns ---

    def _default_ignores(self) -> List[str]:
        """Default ignore patterns."""
        return [
            "node_modules",
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "dist",
            "build",
            ".egg-info",
            ".tox",
            ".pytest_cache",
            "*.pyc",
            "*.pyo",
            "*.min.js",
            "*.map",
            "*.lock",
            ".vivify",
            ".qoder",
            ".ruff_cache",
        ]

    def _should_ignore(self, path: str) -> bool:
        """Check if path matches any ignore pattern."""
        basename = os.path.basename(path)
        for pattern in self.ignore_patterns:
            # Check exact match
            if basename == pattern:
                return True
            # Check glob pattern match
            if fnmatch.fnmatch(basename, pattern):
                return True
            # Check if any path component matches
            if pattern in Path(path).parts:
                return True
        return False

    # --- .knowledgeignore Support ---

    def _load_ignore_file(self) -> List[str]:
        """Load .vivify/knowledge/.knowledgeignore if it exists."""
        ignore_path = self.root / ".vivify" / "knowledge" / ".knowledgeignore"
        if not ignore_path.exists():
            return []

        patterns: List[str] = []
        try:
            content = ignore_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith("#"):
                    patterns.append(line)
        except OSError:
            pass

        return patterns
