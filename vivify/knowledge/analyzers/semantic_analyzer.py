"""Semantic analyzer: enriches structural graph with LLM-generated descriptions.

Key design principles:
1. Single LLM call for ALL modules (cost control)
2. If qodercli wiki exists, extract semantics from it (zero additional cost)
3. Graceful degradation: if LLM fails, leave descriptions empty
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Layer keywords heuristic for fallback classification
_LAYER_HINTS: Dict[str, List[str]] = {
    "api": ["cli", "api", "endpoint", "route", "handler", "view", "controller"],
    "core": ["kernel", "core", "engine", "pipeline", "loop", "dispatch"],
    "data": ["storage", "db", "database", "model", "migration", "repo"],
    "util": ["util", "helper", "tool", "common", "shared", "lib"],
    "config": ["config", "setting", "schema", "defaults", "loader"],
    "test": ["test", "spec", "fixture", "mock", "conftest"],
}


@dataclass
class ModuleSemantics:
    """Semantic information for a module."""

    name: str
    description: str = ""  # 一句话描述
    responsibility: str = ""  # 职责说明（2-3句）
    layer: str = ""  # 架构层级: api, core, data, util, config, test
    tags: List[str] = field(default_factory=list)  # 标签

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "responsibility": self.responsibility,
            "layer": self.layer,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModuleSemantics":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            responsibility=data.get("responsibility", ""),
            layer=data.get("layer", ""),
            tags=data.get("tags", []),
        )


class SemanticAnalyzer:
    """Enriches structural analysis with semantic understanding."""

    def __init__(
        self,
        project_root: Path,
        qodercli_binary: str = "qodercli",
        wiki_path: str = "",
        permission_mode: str = "bypass_permissions",
    ):
        self.root = project_root
        self.qodercli = qodercli_binary
        self.wiki_path = wiki_path
        self.permission_mode = permission_mode

    def analyze(self, modules: List[dict]) -> List[ModuleSemantics]:
        """Generate semantic descriptions for modules.

        Args:
            modules: List of dicts with keys: name, path, files (list of filenames),
                     exports (list of symbol names), dependencies (list of module names),
                     total_lines, complexity

        Strategy:
        1. Try to extract from wiki if available (zero cost)
        2. Fall back to single LLM call for all modules
        3. If both fail, return empty semantics
        """
        if not modules:
            return []

        # 先尝试从 wiki 提取
        wiki_semantics = self._extract_from_wiki(modules)
        if wiki_semantics and len(wiki_semantics) >= len(modules) * 0.7:
            # wiki 覆盖了 70%+ 的模块，直接用
            return self._fill_missing(wiki_semantics, modules)

        # 否则调用 LLM
        llm_semantics = self._analyze_via_llm(modules)
        if llm_semantics:
            return llm_semantics

        # 全部失败，返回空语义（仅用模块名作为描述）
        return self._fallback_semantics(modules)

    def analyze_incremental(
        self,
        changed_modules: List[dict],
        existing_semantics: List[ModuleSemantics],
    ) -> List[ModuleSemantics]:
        """Incremental: only re-analyze changed modules, keep existing for unchanged."""
        if not changed_modules:
            return list(existing_semantics)

        # Build lookup of existing semantics by name
        existing_map: Dict[str, ModuleSemantics] = {
            s.name: s for s in existing_semantics
        }

        # Analyze only changed modules
        new_semantics = self.analyze(changed_modules)
        new_map: Dict[str, ModuleSemantics] = {s.name: s for s in new_semantics}

        # Merge: update changed, keep existing for unchanged
        existing_map.update(new_map)
        return list(existing_map.values())

    def _extract_from_wiki(self, modules: List[dict]) -> Optional[List[ModuleSemantics]]:
        """Extract module semantics from existing qodercli wiki.

        Reads .qoder/repowiki/zh/meta/repowiki-metadata.json and maps
        wiki sections to modules by path matching.
        """
        wiki_dir = self.root / (self.wiki_path or ".qoder/repowiki/zh")
        meta_path = wiki_dir / "meta" / "repowiki-metadata.json"
        if not meta_path.exists():
            return None

        try:
            with meta_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("Failed to load wiki metadata: %s", exc)
            return None

        if not isinstance(data, dict):
            return None

        # Extract overview text for context
        overview = self._get_wiki_overview_text(data.get("wiki_overview"))

        # Extract catalog info for module matching
        catalogs = data.get("wiki_catalogs", [])
        if not isinstance(catalogs, list):
            catalogs = []

        # Extract source file paths for module matching
        source_files = data.get("source_files", [])
        if not isinstance(source_files, list):
            source_files = []

        source_paths = set()
        for sf in source_files:
            if isinstance(sf, dict):
                p = sf.get("path") or sf.get("filename") or ""
                if p:
                    source_paths.add(p)
            elif isinstance(sf, str):
                source_paths.add(sf)

        # Build module-to-catalog mapping
        results: List[ModuleSemantics] = []
        for module in modules:
            mod_name = module.get("name", "")
            mod_path = module.get("path", "")

            # Try to find matching catalog by name/description
            matched_catalog = self._match_catalog(mod_name, mod_path, catalogs)

            description = ""
            responsibility = ""
            if matched_catalog:
                cat_name = matched_catalog.get("name", "")
                cat_desc = matched_catalog.get("description", "")
                cat_prompt = matched_catalog.get("prompt", "")

                description = cat_name if cat_name else mod_name
                # Use prompt text as responsibility hint (truncated)
                if cat_prompt and len(cat_prompt) > 20:
                    responsibility = cat_prompt[:200].strip()
                elif cat_desc:
                    responsibility = cat_desc

            # Determine layer
            layer = self._infer_layer(mod_name, mod_path)

            # Generate tags from module context
            tags = self._generate_tags(mod_name, mod_path, module.get("exports", []))

            if description or responsibility:
                results.append(
                    ModuleSemantics(
                        name=mod_name,
                        description=description,
                        responsibility=responsibility,
                        layer=layer,
                        tags=tags,
                    )
                )

        return results if results else None

    def _get_wiki_overview_text(self, wiki_overview) -> str:
        """Extract overview text from wiki_overview field."""
        if not wiki_overview:
            return ""
        if isinstance(wiki_overview, str):
            return wiki_overview.strip()
        if isinstance(wiki_overview, dict):
            for key in ("content", "text", "summary", "overview"):
                v = wiki_overview.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ""

    def _match_catalog(
        self, mod_name: str, mod_path: str, catalogs: list
    ) -> Optional[dict]:
        """Match a module to a wiki catalog entry by name/path similarity."""
        mod_name_lower = mod_name.lower().replace("_", "").replace("-", "")
        mod_parts = set(mod_name.lower().replace("-", "_").split("_"))

        best_match = None
        best_score = 0

        for cat in catalogs:
            if not isinstance(cat, dict):
                continue

            cat_name = (cat.get("name") or "").lower()
            cat_desc = (cat.get("description") or "").lower()
            dep_files = (cat.get("dependent_files") or "").lower()

            score = 0

            # Check if module name appears in catalog name/description
            cat_name_normalized = cat_name.replace(" ", "").replace("_", "").replace("-", "")
            if mod_name_lower in cat_name_normalized:
                score += 3
            elif cat_name_normalized in mod_name_lower:
                score += 2

            # Check keyword overlap
            cat_words = set(re.split(r"[\s_\-/]+", cat_name + " " + cat_desc))
            overlap = mod_parts & cat_words
            score += len(overlap)

            # Check if module path appears in dependent files
            if mod_path and mod_path.lower() in dep_files:
                score += 5

            if score > best_score:
                best_score = score
                best_match = cat

        # Only return match if score is meaningful
        return best_match if best_score >= 2 else None

    def _analyze_via_llm(self, modules: List[dict]) -> Optional[List[ModuleSemantics]]:
        """Single LLM call to analyze all modules.

        Prompt design:
        - Input: structured module list (name, path, exports, dependencies, complexity)
        - Output: JSON array with description, responsibility, layer, tags for each module
        - Token budget: ~3000-5000 tokens total
        """
        prompt = self._build_analysis_prompt(modules)

        env = dict(os.environ)
        env.setdefault("TERM", "dumb")

        try:
            result = subprocess.run(
                [
                    self.qodercli,
                    "-p",
                    prompt,
                    "--yolo",
                    "-q",
                    "--model",
                    "ultimate",
                    "--max-turns",
                    "1",
                    "--permission-mode",
                    self.permission_mode,
                    "-w",
                    str(self.root),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(self.root),
                env=env,
            )
            if result.returncode != 0:
                logger.warning(
                    "LLM semantic analysis failed: %s", result.stderr[:200]
                )
                return None

            return self._parse_llm_response(result.stdout, modules)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning("Semantic analysis unavailable: %s", e)
            return None

    def _build_analysis_prompt(self, modules: List[dict]) -> str:
        """Build the LLM prompt for module analysis.

        Keep it concise to control token cost. Include:
        - Project name (from cwd)
        - Module list with: name, path, key exports, dependencies, line count
        - Request: JSON array output with description, responsibility, layer, tags
        """
        # 构建紧凑的模块清单
        module_lines = []
        for m in modules:
            exports_str = ", ".join(m.get("exports", [])[:10])  # 最多10个
            deps_str = ", ".join(m.get("dependencies", [])[:5])  # 最多5个
            module_lines.append(
                f"- {m['name']} ({m.get('path', '')}, {m.get('total_lines', 0)} lines): "
                f"exports=[{exports_str}], deps=[{deps_str}]"
            )

        modules_block = "\n".join(module_lines)
        project_name = self.root.name

        prompt = (
            f"分析以下项目 '{project_name}' 的模块架构。"
            f"对每个模块给出：description(一句话)、responsibility(职责2-3句)、"
            f"layer(api/core/data/util/config/test之一)、tags(2-3个标签)。\n\n"
            f"## 模块列表\n{modules_block}\n\n"
            f"## 输出格式\n"
            f"返回 JSON 数组（用 ```json``` 代码块包裹）：\n"
            f'```json\n[\n  {{"name": "模块名", "description": "...", '
            f'"responsibility": "...", "layer": "...", "tags": ["...", "..."]}}\n]\n```\n\n'
            f"只返回 JSON，不要其他文字。"
        )

        return prompt

    def _parse_llm_response(
        self, output: str, modules: List[dict]
    ) -> Optional[List[ModuleSemantics]]:
        """Parse LLM JSON response into ModuleSemantics list."""
        if not output or not output.strip():
            return None

        # 提取 ```json ... ``` 块
        json_match = re.search(r"```json\s*\n?(.*?)\n?\s*```", output, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # Try to find raw JSON array
            array_match = re.search(r"\[\s*\{.*\}\s*\]", output, re.DOTALL)
            if array_match:
                json_str = array_match.group(0)
            else:
                logger.warning("No JSON found in LLM response")
                return None

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse LLM JSON: %s", exc)
            return None

        if not isinstance(parsed, list):
            return None

        # Build module name set for validation
        module_names = {m["name"] for m in modules}

        results: List[ModuleSemantics] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            if name not in module_names:
                # Try fuzzy match
                name = self._fuzzy_match_name(name, module_names)
                if not name:
                    continue

            layer = item.get("layer", "")
            if layer not in ("api", "core", "data", "util", "config", "test"):
                layer = self._infer_layer(name, "")

            tags = item.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            tags = [str(t) for t in tags if t][:5]

            results.append(
                ModuleSemantics(
                    name=name,
                    description=str(item.get("description", ""))[:200],
                    responsibility=str(item.get("responsibility", ""))[:500],
                    layer=layer,
                    tags=tags,
                )
            )

        return results if results else None

    def _fuzzy_match_name(self, name: str, valid_names: set) -> str:
        """Try to match an LLM-returned name to valid module names."""
        if not name:
            return ""
        name_lower = name.lower().replace(" ", "_").replace("-", "_")
        for valid in valid_names:
            if valid.lower() == name_lower:
                return valid
            if name_lower in valid.lower() or valid.lower() in name_lower:
                return valid
        return ""

    def _fill_missing(
        self, partial: List[ModuleSemantics], modules: List[dict]
    ) -> List[ModuleSemantics]:
        """Fill in missing modules with fallback descriptions."""
        covered = {s.name for s in partial}
        result = list(partial)

        for m in modules:
            name = m.get("name", "")
            if name and name not in covered:
                result.append(
                    ModuleSemantics(
                        name=name,
                        description=self._name_to_description(name),
                        responsibility="",
                        layer=self._infer_layer(name, m.get("path", "")),
                        tags=self._generate_tags(
                            name, m.get("path", ""), m.get("exports", [])
                        ),
                    )
                )

        return result

    def _fallback_semantics(self, modules: List[dict]) -> List[ModuleSemantics]:
        """Generate minimal semantics from module names alone."""
        results: List[ModuleSemantics] = []
        for m in modules:
            name = m.get("name", "")
            path = m.get("path", "")
            exports = m.get("exports", [])

            results.append(
                ModuleSemantics(
                    name=name,
                    description=self._name_to_description(name),
                    responsibility="",
                    layer=self._infer_layer(name, path),
                    tags=self._generate_tags(name, path, exports),
                )
            )
        return results

    def _infer_layer(self, name: str, path: str) -> str:
        """Heuristic layer classification from name/path keywords."""
        text = f"{name} {path}".lower()
        for layer, keywords in _LAYER_HINTS.items():
            for kw in keywords:
                if kw in text:
                    return layer
        return "core"  # default

    def _generate_tags(self, name: str, path: str, exports: List[str]) -> List[str]:
        """Generate tags from module context."""
        tags: List[str] = []

        # Add name-derived tag
        parts = name.lower().replace("-", "_").split("_")
        tags.extend(p for p in parts if len(p) > 2 and p not in ("the", "and", "for"))

        # Add export-derived tags (class/function names as hints)
        for exp in exports[:3]:
            if len(exp) > 3:
                tags.append(exp.lower())

        # Deduplicate and limit
        seen: set = set()
        unique_tags: List[str] = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                unique_tags.append(t)
            if len(unique_tags) >= 3:
                break

        return unique_tags

    def _name_to_description(self, name: str) -> str:
        """Convert module name to a human-readable description."""
        return name.replace("_", " ").replace("-", " ").title() + " module"
