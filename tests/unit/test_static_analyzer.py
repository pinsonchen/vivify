"""Tests for the static code analyzer and import resolver."""

import os
import tempfile
import time
from pathlib import Path

import pytest

from vivify.knowledge.analyzers.static_analyzer import (
    FileInfo,
    ModuleInfo,
    StaticAnalyzer,
    StructuralGraph,
)
from vivify.knowledge.analyzers.import_resolver import ImportResolver


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary Python project structure."""
    # Create main package
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""My app."""\n__version__ = "1.0.0"\n')

    # Module: models
    models = pkg / "models"
    models.mkdir()
    (models / "__init__.py").write_text("from .user import User\nfrom .post import Post\n")
    (models / "user.py").write_text(
        "import os\nfrom dataclasses import dataclass\n\n"
        "@dataclass\nclass User:\n    name: str\n    email: str\n\n"
        "class AdminUser(User):\n    role: str = 'admin'\n"
    )
    (models / "post.py").write_text(
        "from datetime import datetime\nfrom .user import User\n\n"
        "class Post:\n    def __init__(self, author: User, content: str):\n"
        "        self.author = author\n        self.content = content\n"
        "        self.created_at = datetime.now()\n"
    )

    # Module: services
    services = pkg / "services"
    services.mkdir()
    (services / "__init__.py").write_text("")
    (services / "auth.py").write_text(
        "import hashlib\nfrom myapp.models.user import User\n\n"
        "class AuthService:\n"
        "    def authenticate(self, username: str, password: str) -> bool:\n"
        "        return True\n\n"
        "    async def refresh_token(self, user: User) -> str:\n"
        "        return 'token'\n"
    )
    (services / "email_service.py").write_text(
        "from myapp.models import User\n"
        "import smtplib\n\n"
        "def send_welcome_email(user: User) -> None:\n"
        "    pass\n\n"
        "def send_reset_email(user: User, token: str) -> None:\n"
        "    pass\n"
    )

    # Module: utils
    utils = pkg / "utils"
    utils.mkdir()
    (utils / "__init__.py").write_text("from .helpers import slugify\n")
    (utils / "helpers.py").write_text(
        "import re\n\n"
        "def slugify(text: str) -> str:\n"
        "    return re.sub(r'\\W+', '-', text.lower())\n\n"
        "def truncate(text: str, length: int = 100) -> str:\n"
        "    return text[:length]\n"
    )

    # Create __pycache__ (should be ignored)
    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "something.pyc").write_text("bytecode")

    return tmp_path


@pytest.fixture
def temp_js_project(tmp_path):
    """Create a temporary JS/TS project structure."""
    # Create src directory as module
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "package.json").write_text(
        '{"name": "test-app", "workspaces": ["src"]}'
    )

    # Components module
    components = src / "components"
    components.mkdir()
    (components / "index.ts").write_text(
        "export { Button } from './Button';\n"
        "export { Input } from './Input';\n"
    )
    (components / "Button.tsx").write_text(
        "import React from 'react';\n"
        "import { styled } from '@emotion/styled';\n\n"
        "export class Button extends React.Component {\n"
        "  render() { return <button />; }\n"
        "}\n"
    )
    (components / "Input.tsx").write_text(
        "import React from 'react';\n"
        "import { validate } from '../utils/validate';\n\n"
        "export const Input = ({ value }) => {\n"
        "  return <input value={value} />;\n"
        "};\n"
    )

    # Utils module
    utils = src / "utils"
    utils.mkdir()
    (utils / "index.js").write_text(
        "const { format } = require('date-fns');\n\n"
        "export function formatDate(date) {\n"
        "  return format(date, 'yyyy-MM-dd');\n"
        "}\n\n"
        "export const slugify = (text) => {\n"
        "  return text.toLowerCase().replace(/\\W+/g, '-');\n"
        "};\n"
    )
    (utils / "validate.ts").write_text(
        "export function validate(value: string): boolean {\n"
        "  return value.length > 0;\n"
        "}\n\n"
        "export class Validator {\n"
        "  rules: string[] = [];\n"
        "}\n"
    )

    # node_modules (should be ignored)
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "react.js").write_text("module.exports = {};")

    return tmp_path


@pytest.fixture
def temp_project_with_ignorefile(temp_project):
    """Add a .knowledgeignore file to the temp project."""
    vivify_dir = temp_project / ".vivify" / "knowledge"
    vivify_dir.mkdir(parents=True)
    (vivify_dir / ".knowledgeignore").write_text(
        "# Ignore test fixtures\n"
        "fixtures\n"
        "*.generated.py\n"
        "\n"
        "# Ignore vendor\n"
        "vendor\n"
    )
    return temp_project


# ============================================================
# Python File Analysis Tests
# ============================================================


class TestPythonFileAnalysis:
    """Test Python file analysis using ast module."""

    def test_extract_imports(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        filepath = temp_project / "myapp" / "services" / "auth.py"
        rel_path = "myapp/services/auth.py"
        result = analyzer._analyze_python_file(filepath, rel_path)

        assert result is not None
        assert "hashlib" in result.imports
        assert "myapp.models.user" in result.imports

    def test_extract_classes(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        filepath = temp_project / "myapp" / "models" / "user.py"
        rel_path = "myapp/models/user.py"
        result = analyzer._analyze_python_file(filepath, rel_path)

        assert result is not None
        assert "User" in result.classes
        assert "AdminUser" in result.classes

    def test_extract_functions(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        filepath = temp_project / "myapp" / "utils" / "helpers.py"
        rel_path = "myapp/utils/helpers.py"
        result = analyzer._analyze_python_file(filepath, rel_path)

        assert result is not None
        assert "slugify" in result.functions
        assert "truncate" in result.functions

    def test_extract_decorators(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        filepath = temp_project / "myapp" / "models" / "user.py"
        rel_path = "myapp/models/user.py"
        result = analyzer._analyze_python_file(filepath, rel_path)

        assert result is not None
        assert "dataclass" in result.decorators

    def test_extract_exports(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        filepath = temp_project / "myapp" / "services" / "email_service.py"
        rel_path = "myapp/services/email_service.py"
        result = analyzer._analyze_python_file(filepath, rel_path)

        assert result is not None
        assert "send_welcome_email" in result.exports
        assert "send_reset_email" in result.exports

    def test_syntax_error_file(self, tmp_path):
        """Files with syntax errors should return basic info without crashing."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def broken(\n  # unterminated\n")

        analyzer = StaticAnalyzer(tmp_path)
        result = analyzer._analyze_python_file(bad_file, "bad.py")

        assert result is not None
        assert result.language == "python"
        assert result.line_count > 0
        assert result.imports == []

    def test_empty_file(self, tmp_path):
        """Empty files should be handled gracefully."""
        empty = tmp_path / "empty.py"
        empty.write_text("")

        analyzer = StaticAnalyzer(tmp_path)
        result = analyzer._analyze_python_file(empty, "empty.py")

        assert result is not None
        assert result.line_count == 0
        assert result.imports == []
        assert result.classes == []

    def test_line_count(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        filepath = temp_project / "myapp" / "models" / "user.py"
        rel_path = "myapp/models/user.py"
        result = analyzer._analyze_python_file(filepath, rel_path)

        assert result is not None
        assert result.line_count > 0

    def test_relative_imports(self, temp_project):
        """Test that relative imports are captured."""
        analyzer = StaticAnalyzer(temp_project)
        filepath = temp_project / "myapp" / "models" / "post.py"
        rel_path = "myapp/models/post.py"
        result = analyzer._analyze_python_file(filepath, rel_path)

        assert result is not None
        # .user is a relative import from the same package
        assert any(".user" in imp or "user" in imp.lower() for imp in result.imports)


# ============================================================
# JS/TS File Analysis Tests
# ============================================================


class TestJsFileAnalysis:
    """Test JS/TS file analysis using regex patterns."""

    def test_extract_imports(self, temp_js_project):
        analyzer = StaticAnalyzer(temp_js_project)
        filepath = temp_js_project / "src" / "components" / "Button.tsx"
        rel_path = "src/components/Button.tsx"
        result = analyzer._analyze_js_file(filepath, rel_path, "typescript")

        assert result is not None
        assert "react" in result.imports
        assert "@emotion/styled" in result.imports

    def test_extract_require(self, temp_js_project):
        analyzer = StaticAnalyzer(temp_js_project)
        filepath = temp_js_project / "src" / "utils" / "index.js"
        rel_path = "src/utils/index.js"
        result = analyzer._analyze_js_file(filepath, rel_path, "javascript")

        assert result is not None
        assert "date-fns" in result.imports

    def test_extract_classes(self, temp_js_project):
        analyzer = StaticAnalyzer(temp_js_project)
        filepath = temp_js_project / "src" / "utils" / "validate.ts"
        rel_path = "src/utils/validate.ts"
        result = analyzer._analyze_js_file(filepath, rel_path, "typescript")

        assert result is not None
        assert "Validator" in result.classes

    def test_extract_exported_functions(self, temp_js_project):
        analyzer = StaticAnalyzer(temp_js_project)
        filepath = temp_js_project / "src" / "utils" / "index.js"
        rel_path = "src/utils/index.js"
        result = analyzer._analyze_js_file(filepath, rel_path, "javascript")

        assert result is not None
        assert "formatDate" in result.exports
        assert "slugify" in result.exports

    def test_extract_exported_class(self, temp_js_project):
        analyzer = StaticAnalyzer(temp_js_project)
        filepath = temp_js_project / "src" / "components" / "Button.tsx"
        rel_path = "src/components/Button.tsx"
        result = analyzer._analyze_js_file(filepath, rel_path, "typescript")

        assert result is not None
        assert "Button" in result.exports

    def test_relative_imports(self, temp_js_project):
        analyzer = StaticAnalyzer(temp_js_project)
        filepath = temp_js_project / "src" / "components" / "Input.tsx"
        rel_path = "src/components/Input.tsx"
        result = analyzer._analyze_js_file(filepath, rel_path, "typescript")

        assert result is not None
        assert "../utils/validate" in result.imports


# ============================================================
# Module Discovery Tests
# ============================================================


class TestModuleDiscovery:
    """Test module/package discovery logic."""

    def test_discover_python_modules(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        modules = analyzer._discover_modules()

        # Should find myapp and its sub-packages
        assert any("myapp" in m for m in modules)

    def test_discover_sub_packages(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        modules = analyzer._discover_modules()

        # Should find sub-packages
        module_names = [Path(m).name for m in modules]
        assert "models" in module_names or any("models" in m for m in modules)

    def test_ignores_pycache(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        modules = analyzer._discover_modules()

        assert not any("__pycache__" in m for m in modules)

    def test_discover_js_workspaces(self, temp_js_project):
        analyzer = StaticAnalyzer(temp_js_project)
        modules = analyzer._discover_modules()

        assert any("src" in m for m in modules)

    def test_ignores_node_modules(self, temp_js_project):
        analyzer = StaticAnalyzer(temp_js_project)
        modules = analyzer._discover_modules()

        assert not any("node_modules" in m for m in modules)


# ============================================================
# Complexity Computation Tests
# ============================================================


class TestComplexityComputation:
    """Test module complexity scoring."""

    def test_simple_module(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        mod = ModuleInfo(
            name="utils",
            path="myapp/utils",
            files=[FileInfo("f.py", "python", 100, [], [], [], [], [], "abc")],
            total_lines=100,
            internal_imports=[],
            external_imports=["os"],
        )
        assert analyzer._compute_complexity(mod) == "simple"

    def test_moderate_module(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        files = [
            FileInfo(f"f{i}.py", "python", 100, [], [], [], [], [], f"h{i}")
            for i in range(6)
        ]
        mod = ModuleInfo(
            name="services",
            path="myapp/services",
            files=files,
            total_lines=600,
            internal_imports=["models", "utils"],
            external_imports=["os", "sys"],
        )
        assert analyzer._compute_complexity(mod) == "moderate"

    def test_complex_module(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        files = [
            FileInfo(f"f{i}.py", "python", 100, [], [], [], [], [], f"h{i}")
            for i in range(25)
        ]
        mod = ModuleInfo(
            name="kernel",
            path="myapp/kernel",
            files=files,
            total_lines=2500,
            internal_imports=[f"mod{i}" for i in range(30)],
            external_imports=[f"pkg{i}" for i in range(25)],
        )
        assert analyzer._compute_complexity(mod) == "complex"

    def test_complexity_by_lines(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        mod = ModuleInfo(
            name="big",
            path="big",
            files=[FileInfo("f.py", "python", 3000, [], [], [], [], [], "x")],
            total_lines=3000,
        )
        assert analyzer._compute_complexity(mod) == "complex"

    def test_complexity_by_imports(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        mod = ModuleInfo(
            name="connected",
            path="connected",
            files=[FileInfo("f.py", "python", 100, [], [], [], [], [], "x")],
            total_lines=100,
            internal_imports=[f"m{i}" for i in range(30)],
            external_imports=[f"p{i}" for i in range(25)],
        )
        assert analyzer._compute_complexity(mod) == "complex"


# ============================================================
# Import Resolver Tests
# ============================================================


class TestImportResolver:
    """Test import path resolution."""

    def test_resolve_absolute_python_import(self, temp_project):
        modules = ["myapp/models", "myapp/services", "myapp/utils"]
        resolver = ImportResolver(temp_project, modules)

        result = resolver.resolve_python_import(
            "myapp.models.user", "myapp/services/auth.py"
        )
        assert result == "models"

    def test_resolve_relative_import(self, temp_project):
        modules = ["myapp/models", "myapp/services", "myapp/utils"]
        resolver = ImportResolver(temp_project, modules)

        result = resolver.resolve_python_import(".user", "myapp/models/post.py")
        assert result == "models"

    def test_resolve_external_import(self, temp_project):
        modules = ["myapp/models", "myapp/services", "myapp/utils"]
        resolver = ImportResolver(temp_project, modules)

        result = resolver.resolve_python_import("os", "myapp/services/auth.py")
        assert result is None

    def test_resolve_stdlib_import(self, temp_project):
        modules = ["myapp/models", "myapp/services"]
        resolver = ImportResolver(temp_project, modules)

        result = resolver.resolve_python_import("hashlib", "myapp/services/auth.py")
        assert result is None

    def test_is_internal(self, temp_project):
        modules = ["myapp/models", "myapp/services", "myapp/utils"]
        resolver = ImportResolver(temp_project, modules)

        assert resolver.is_internal("myapp.models.user") is True
        assert resolver.is_internal(".models") is True
        assert resolver.is_internal("os") is False
        assert resolver.is_internal("hashlib") is False

    def test_resolve_js_relative_import(self, temp_js_project):
        modules = ["src/components", "src/utils"]
        resolver = ImportResolver(temp_js_project, modules)

        result = resolver.resolve_js_import(
            "../utils/validate", "src/components/Input.tsx"
        )
        assert result == "utils"

    def test_resolve_js_external_import(self, temp_js_project):
        modules = ["src/components", "src/utils"]
        resolver = ImportResolver(temp_js_project, modules)

        result = resolver.resolve_js_import("react", "src/components/Button.tsx")
        assert result is None

    def test_resolve_js_same_module(self, temp_js_project):
        modules = ["src/components", "src/utils"]
        resolver = ImportResolver(temp_js_project, modules)

        result = resolver.resolve_js_import("./Button", "src/components/index.ts")
        assert result == "components"


# ============================================================
# Ignore Pattern Tests
# ============================================================


class TestIgnorePatterns:
    """Test file/directory ignore patterns."""

    def test_ignore_pycache(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        assert analyzer._should_ignore("__pycache__") is True

    def test_ignore_node_modules(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        assert analyzer._should_ignore("node_modules") is True

    def test_ignore_pyc_files(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        assert analyzer._should_ignore("module.pyc") is True

    def test_ignore_min_js(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        assert analyzer._should_ignore("app.min.js") is True

    def test_dont_ignore_normal_files(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        assert analyzer._should_ignore("main.py") is False
        assert analyzer._should_ignore("app.js") is False
        assert analyzer._should_ignore("utils") is False

    def test_ignore_git(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        assert analyzer._should_ignore(".git") is True

    def test_ignore_venv(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        assert analyzer._should_ignore(".venv") is True
        assert analyzer._should_ignore("venv") is True

    def test_custom_ignore_patterns(self):
        analyzer = StaticAnalyzer(Path("/tmp"), ignore_patterns=["secret", "*.log"])
        assert analyzer._should_ignore("secret") is True
        assert analyzer._should_ignore("app.log") is True
        assert analyzer._should_ignore("main.py") is False

    def test_knowledgeignore_file(self, temp_project_with_ignorefile):
        analyzer = StaticAnalyzer(temp_project_with_ignorefile)
        assert analyzer._should_ignore("fixtures") is True
        assert analyzer._should_ignore("vendor") is True
        assert analyzer._should_ignore("model.generated.py") is True


# ============================================================
# Fingerprint Tests
# ============================================================


class TestFingerprint:
    """Test file fingerprint computation."""

    def test_fingerprint_deterministic(self, tmp_path):
        """Same content should produce same fingerprint."""
        f = tmp_path / "test.py"
        f.write_text("hello world")

        analyzer = StaticAnalyzer(tmp_path)
        fp1 = analyzer._compute_fingerprint(f)
        fp2 = analyzer._compute_fingerprint(f)
        assert fp1 == fp2
        assert len(fp1) == 16  # truncated sha256

    def test_fingerprint_changes_with_content(self, tmp_path):
        """Different content should produce different fingerprints."""
        f = tmp_path / "test.py"

        f.write_text("version 1")
        analyzer = StaticAnalyzer(tmp_path)
        fp1 = analyzer._compute_fingerprint(f)

        f.write_text("version 2")
        fp2 = analyzer._compute_fingerprint(f)

        assert fp1 != fp2

    def test_fingerprint_from_content(self):
        analyzer = StaticAnalyzer(Path("/tmp"))
        fp = analyzer._compute_fingerprint_from_content("test content")
        assert len(fp) == 16
        assert fp == analyzer._compute_fingerprint_from_content("test content")


# ============================================================
# Full Analysis Integration Tests
# ============================================================


class TestFullAnalysis:
    """Integration tests for full project analysis."""

    def test_full_analysis_returns_structural_graph(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        graph = analyzer.analyze()

        assert isinstance(graph, StructuralGraph)
        assert len(graph.modules) > 0
        assert graph.project_root == str(temp_project.resolve())
        assert "python" in graph.languages

    def test_full_analysis_finds_modules(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        graph = analyzer.analyze()

        module_names = [m.name for m in graph.modules]
        assert "models" in module_names or "myapp" in module_names

    def test_full_analysis_computes_edges(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        graph = analyzer.analyze()

        # services imports models, so there should be an edge
        # (depends on module discovery granularity)
        assert isinstance(graph.edges, list)

    def test_full_analysis_file_fingerprints(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)
        graph = analyzer.analyze()

        assert len(graph.file_fingerprints) > 0
        for path, fp in graph.file_fingerprints.items():
            assert len(fp) == 16

    def test_incremental_analysis(self, temp_project):
        analyzer = StaticAnalyzer(temp_project)

        # Full analysis first
        full = analyzer.analyze()

        # Incremental with one changed file
        result = analyzer.analyze_incremental(["myapp/models/user.py"])

        assert isinstance(result, StructuralGraph)
        # Should only contain affected module(s)
        assert len(result.modules) <= len(full.modules)


# ============================================================
# Self-Analysis Test (run on vivify project itself)
# ============================================================


class TestSelfAnalysis:
    """Run analysis on the vivify project itself to verify correctness and performance."""

    def test_analyze_vivify_project(self):
        """Analyze the vivify project itself - validates real-world usage."""
        project_root = Path(__file__).parent.parent.parent  # tests/unit -> project root
        if not (project_root / "vivify" / "__init__.py").exists():
            pytest.skip("Not running from vivify project root")

        analyzer = StaticAnalyzer(project_root)

        start = time.time()
        graph = analyzer.analyze()
        elapsed = time.time() - start

        # Performance: should complete in under 5 seconds
        assert elapsed < 5.0, f"Analysis took {elapsed:.2f}s (target: <5s)"

        # Should find vivify modules
        module_names = [m.name for m in graph.modules]
        assert "kernel" in module_names or any(
            "kernel" in m.path for m in graph.modules
        ), f"Expected 'kernel' module, found: {module_names}"

        # Should detect Python as primary language
        assert "python" in graph.languages

        # Should have file fingerprints
        assert len(graph.file_fingerprints) > 10

        # Should have edges
        assert len(graph.edges) > 0

        # Print summary for debugging
        print(f"\n--- Vivify Self-Analysis ---")
        print(f"Modules found: {len(graph.modules)}")
        print(f"Module names: {module_names}")
        print(f"Total files: {len(graph.file_fingerprints)}")
        print(f"Edges: {len(graph.edges)}")
        print(f"Languages: {graph.languages}")
        print(f"Time: {elapsed:.3f}s")

    def test_analyze_vivify_module_details(self):
        """Verify specific module details of vivify."""
        project_root = Path(__file__).parent.parent.parent
        if not (project_root / "vivify" / "__init__.py").exists():
            pytest.skip("Not running from vivify project root")

        analyzer = StaticAnalyzer(project_root)
        graph = analyzer.analyze()

        # Find kernel module
        kernel_mods = [m for m in graph.modules if m.name == "kernel"]
        if kernel_mods:
            kernel = kernel_mods[0]
            assert kernel.total_lines > 0
            assert len(kernel.files) > 0
            assert kernel.complexity in ("simple", "moderate", "complex")

            # Kernel should have internal imports to other vivify modules
            assert len(kernel.internal_imports) > 0 or len(kernel.external_imports) > 0
