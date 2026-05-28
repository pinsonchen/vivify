"""Resolve import paths to actual module/file locations.

Provides resolution of Python and JS/TS import statements to their
source module within the project, distinguishing internal from external imports.
"""

from pathlib import Path
from typing import Dict, List, Optional
import os


class ImportResolver:
    """Resolves import statements to their source modules.

    Given an import path and the file it appears in, determines which
    project module (if any) provides that import.
    """

    def __init__(self, project_root: Path, modules: List[str]):
        """Initialize the resolver.

        Args:
            project_root: Absolute path to the project root.
            modules: List of discovered module relative paths (e.g. ["vivify/kernel", "vivify/agents"]).
        """
        self.root = Path(project_root).resolve()
        self.modules = modules
        self.module_map = self._build_module_map(modules)
        self._top_level_packages = self._discover_top_level_packages()

    def resolve_python_import(self, import_path: str, from_file: str) -> Optional[str]:
        """Resolve a Python import to its source module name.

        Args:
            import_path: The import string (e.g. "vivify.kernel.loop", ".models", "os")
            from_file: Relative path of the file containing the import.

        Returns:
            Module name if internal, None if external.

        Examples:
            "from vivify.kernel.loop import Kernel" -> "kernel"
            "import os" -> None (external)
            "from .models import Feature" -> resolves relative to from_file's module
        """
        # Handle relative imports
        if import_path.startswith("."):
            return self._resolve_relative_import(import_path, from_file)

        # Split import into parts
        parts = import_path.split(".")

        # Try to find matching module by progressively longer prefixes
        # e.g. "vivify.kernel.loop" -> try "vivify/kernel/loop", "vivify/kernel", "vivify"
        for i in range(len(parts), 0, -1):
            candidate_path = "/".join(parts[:i])
            if candidate_path in self.module_map:
                return self.module_map[candidate_path]

        # Try dotted path matching against module paths
        for mod_path in self.modules:
            mod_dotted = mod_path.replace("/", ".")
            if import_path.startswith(mod_dotted) or import_path == mod_dotted:
                return Path(mod_path).name

        # Check if first part matches any top-level project package
        if parts[0] in self._top_level_packages:
            # It's internal but we might not have an exact module match
            # Try to find the closest module
            for mod_path in self.modules:
                mod_parts = mod_path.split("/")
                # Check if import aligns with module path
                if len(parts) >= len(mod_parts):
                    if parts[: len(mod_parts)] == mod_parts:
                        return Path(mod_path).name
                # Check last segment match
                if mod_parts[-1] == parts[-1] or (
                    len(parts) > 1 and mod_parts[-1] == parts[1]
                ):
                    return Path(mod_path).name
            # Still internal, return first sub-package if known
            if len(parts) > 1:
                for mod_path in self.modules:
                    if mod_path.startswith(f"{parts[0]}/{parts[1]}"):
                        return Path(mod_path).name
            return parts[0]

        return None

    def resolve_js_import(self, import_path: str, from_file: str) -> Optional[str]:
        """Resolve a JS/TS import to its source module name.

        Args:
            import_path: The import path (e.g. "./utils", "../agents/qodercli", "react")
            from_file: Relative path of the file containing the import.

        Returns:
            Module name if internal, None if external.

        Examples:
            "./utils" -> same module (returns module name of from_file)
            "../agents/qodercli" -> "agents"
            "react" -> None (external)
            "@/components/Button" -> resolves alias
        """
        # External package imports (no relative path indicators)
        if not import_path.startswith(".") and not import_path.startswith("@/"):
            # Could be a scoped package (@org/pkg) or bare module
            if import_path.startswith("@") and "/" in import_path:
                # Scoped package like @org/pkg - check if it's a workspace package
                for mod_path in self.modules:
                    if Path(mod_path).name in import_path:
                        return Path(mod_path).name
            return None

        # Resolve relative path
        from_dir = str(Path(from_file).parent)

        if import_path.startswith("@/"):
            # Alias: @/ typically maps to src/ or project root
            resolved = import_path[2:]  # Remove @/
        else:
            # Relative path resolution
            resolved = os.path.normpath(os.path.join(from_dir, import_path))

        # Find which module the resolved path belongs to
        resolved_parts = Path(resolved).parts
        for mod_path in self.modules:
            mod_parts = Path(mod_path).parts
            # Check if resolved path starts with module path
            if len(resolved_parts) >= len(mod_parts):
                if resolved_parts[: len(mod_parts)] == mod_parts:
                    return Path(mod_path).name

        # Same-module relative import
        from_module = self._get_module_for_file(from_file)
        if from_module and import_path.startswith("./"):
            return from_module

        return self._get_module_for_file(from_file)

    def is_internal(self, import_path: str) -> bool:
        """Check if import is internal to the project.

        Args:
            import_path: The import string to check.

        Returns:
            True if the import refers to project-internal code.
        """
        # Relative imports are always internal
        if import_path.startswith("."):
            return True

        # Check against top-level packages
        top = import_path.split(".")[0]
        if top in self._top_level_packages:
            return True

        # Check JS-style paths
        if import_path.startswith("@/"):
            return True

        # Check if any module name matches
        for mod_path in self.modules:
            mod_name = Path(mod_path).name
            if import_path == mod_name or import_path.startswith(f"{mod_name}/"):
                return True

        return False

    def _resolve_relative_import(self, import_path: str, from_file: str) -> Optional[str]:
        """Resolve a Python relative import.

        Args:
            import_path: Relative import (e.g. ".models", "..kernel.loop")
            from_file: File containing the import.

        Returns:
            Module name the import resolves to.
        """
        # Count leading dots
        dots = 0
        for ch in import_path:
            if ch == ".":
                dots += 1
            else:
                break

        # Navigate up from current file
        from_parts = Path(from_file).parts
        if dots <= len(from_parts):
            # Go up 'dots' levels from file's directory
            base_parts = from_parts[: -(dots)]
            remaining = import_path[dots:]

            if remaining:
                target_parts = list(base_parts) + remaining.split(".")
            else:
                target_parts = list(base_parts)

            # Find the module this resolves to
            target_path = "/".join(target_parts)
            for mod_path in self.modules:
                if target_path.startswith(mod_path) or mod_path.startswith(target_path):
                    return Path(mod_path).name

        # Fallback: same module as the importing file
        return self._get_module_for_file(from_file)

    def _build_module_map(self, modules: List[str]) -> Dict[str, str]:
        """Build mapping from various path forms to module names.

        Creates a lookup table that maps different representations of a
        module path to the module's short name.
        """
        mapping: Dict[str, str] = {}
        for mod_path in modules:
            name = Path(mod_path).name
            # Map the full path
            mapping[mod_path] = name
            # Map dotted form
            mapping[mod_path.replace("/", ".")] = name
            # Map just the name
            mapping[name] = name
            # Map path segments
            parts = mod_path.split("/")
            for i in range(len(parts)):
                sub = "/".join(parts[i:])
                if sub not in mapping:
                    mapping[sub] = name

        return mapping

    def _get_module_for_file(self, filepath: str) -> Optional[str]:
        """Get the module name that contains a given file."""
        for mod_path in sorted(self.modules, key=len, reverse=True):
            if filepath.startswith(mod_path):
                return Path(mod_path).name
        return None

    def _discover_top_level_packages(self) -> set:
        """Discover top-level package names in the project."""
        packages = set()
        for entry in os.listdir(self.root):
            full = self.root / entry
            if full.is_dir() and (full / "__init__.py").exists():
                packages.add(entry)
        return packages
