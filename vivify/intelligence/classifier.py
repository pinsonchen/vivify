"""项目场景分类器。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .scanner import ProjectSignals


class ScenarioType(Enum):
    STATIC_SITE = "static-site"
    WEB_APP = "web-app"
    API_SERVICE = "api-service"
    PYTHON_PACKAGE = "python-package"
    CLI_TOOL = "cli-tool"
    DOCS_ONLY = "docs-only"
    MOBILE_APP = "mobile-app"
    MONOREPO = "monorepo"
    INFRA = "infra"
    GENERIC = "generic"


@dataclass
class ProjectProfile:
    primary_scenario: ScenarioType
    secondary_scenarios: list[ScenarioType]
    confidence: float
    language: str
    framework: str | None
    reasoning: str


# 文档/标记类扩展名（用于 docs-only 占比判断）
DOC_EXTS = {".md", ".markdown", ".rst", ".txt", ".adoc", ".asciidoc"}

# 主语言判定
LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".scala": "scala",
    ".dart": "dart",
    ".sh": "shell",
}

WEB_FRAMEWORK_MARKS = {"react", "vue", "next", "nuxt", "angular", "svelte", "astro"}
SERVER_FRAMEWORK_MARKS = {"express", "nestjs", "koa", "fastify", "django", "flask", "fastapi", "starlette", "tornado", "spring", "rails"}


class Classifier:
    """基于项目信号对项目进行场景分类。"""

    def classify(self, signals: ProjectSignals) -> ProjectProfile:
        language = self._detect_language(signals)
        framework = signals.detected_frameworks[0] if signals.detected_frameworks else None
        secondary: list[ScenarioType] = []
        files_lower = {f.lower() for f in signals.files}

        # 1. docs-only：代码文件占比 < 10%
        code_count = sum(c for ext, c in signals.file_extensions.items() if ext.lower() not in DOC_EXTS and ext.lower() not in {".yml", ".yaml", ".json", ".toml", ".lock", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf"})
        total = max(signals.total_files, 1)
        if signals.total_files > 0 and (code_count / total) < 0.10:
            return ProjectProfile(
                primary_scenario=ScenarioType.DOCS_ONLY,
                secondary_scenarios=secondary,
                confidence=0.85,
                language=language,
                framework=framework,
                reasoning=f"代码文件占比 {code_count}/{signals.total_files}<10%，判定为文档项目",
            )

        has_index_html = any(f == "index.html" or f.endswith("/index.html") for f in files_lower)

        # 2. 有 index.html + 无 package.json → static-site
        if has_index_html and not signals.has_package_json:
            return ProjectProfile(
                primary_scenario=ScenarioType.STATIC_SITE,
                secondary_scenarios=secondary,
                confidence=0.8,
                language=language,
                framework=framework,
                reasoning="存在 index.html 且无 package.json",
            )

        # monorepo 提前识别（在 web-app 之前不抢，仅当强标志时优先）
        is_monorepo = (
            "lerna.json" in files_lower
            or "nx.json" in files_lower
            or "pnpm-workspace.yaml" in files_lower
            or "__workspaces__" in signals._dependencies
        )

        web_framework = next((f for f in signals.detected_frameworks if f in WEB_FRAMEWORK_MARKS), None)
        server_framework = next((f for f in signals.detected_frameworks if f in SERVER_FRAMEWORK_MARKS), None)

        # 3. package.json + Web 框架 → web-app
        if signals.has_package_json and web_framework:
            if is_monorepo:
                secondary.append(ScenarioType.MONOREPO)
            return ProjectProfile(
                primary_scenario=ScenarioType.WEB_APP,
                secondary_scenarios=secondary,
                confidence=0.9,
                language=language,
                framework=web_framework,
                reasoning=f"package.json 中检测到 Web 框架 {web_framework}",
            )

        # 4. package.json + index.html + 无框架 → static-site
        if signals.has_package_json and has_index_html and not web_framework and not server_framework:
            return ProjectProfile(
                primary_scenario=ScenarioType.STATIC_SITE,
                secondary_scenarios=secondary,
                confidence=0.7,
                language=language,
                framework=framework,
                reasoning="package.json + index.html 且未检测到 Web/服务端框架",
            )

        # 6（前置）. cli-tool：有 entry_points 或 [project.scripts]
        if signals.entry_points and signals.has_pyproject_toml and not server_framework:
            return ProjectProfile(
                primary_scenario=ScenarioType.CLI_TOOL,
                secondary_scenarios=secondary,
                confidence=0.85,
                language=language or "python",
                framework=framework,
                reasoning=f"pyproject.toml 中声明 [project.scripts]={signals.entry_points[:3]}",
            )

        # 5. python-package
        py_layout = False
        if signals.has_pyproject_toml or (self.repo_has_setup_py(signals)):
            # src/ 目录或与 project_name 同名的包目录
            if "src" in {p.split("/", 1)[0] for p in signals.files}:
                py_layout = True
            elif signals.project_name:
                pkg_name = signals.project_name.replace("-", "_").lower()
                if any(f.lower().startswith(pkg_name + "/") for f in signals.files):
                    py_layout = True
            else:
                py_layout = any(f.endswith("__init__.py") for f in signals.files)

        if py_layout and not server_framework:
            return ProjectProfile(
                primary_scenario=ScenarioType.PYTHON_PACKAGE,
                secondary_scenarios=secondary,
                confidence=0.8,
                language=language or "python",
                framework=framework,
                reasoning="pyproject.toml + 包目录结构，未检测到服务端框架",
            )

        # 7. api-service：服务端框架 / app.py / server.py / main.go / cmd/
        api_marks = []
        if server_framework:
            api_marks.append(f"框架 {server_framework}")
        for f in ("app.py", "server.py", "main.go", "main.py"):
            if f in files_lower:
                api_marks.append(f)
        if any(p.startswith("cmd/") for p in [f.lower() for f in signals.files]):
            api_marks.append("cmd/ 目录")
        if api_marks:
            return ProjectProfile(
                primary_scenario=ScenarioType.API_SERVICE,
                secondary_scenarios=secondary,
                confidence=0.75,
                language=language,
                framework=server_framework or framework,
                reasoning="检测到服务端标志：" + ", ".join(api_marks),
            )

        # 8. mobile-app
        if (
            (self.repo_root_has_dir(signals, "android"))
            or (self.repo_root_has_dir(signals, "ios"))
            or "pubspec.yaml" in files_lower
        ):
            return ProjectProfile(
                primary_scenario=ScenarioType.MOBILE_APP,
                secondary_scenarios=secondary,
                confidence=0.8,
                language=language,
                framework=framework,
                reasoning="检测到 android/ ios/ 或 pubspec.yaml",
            )

        # 9. monorepo
        if is_monorepo:
            return ProjectProfile(
                primary_scenario=ScenarioType.MONOREPO,
                secondary_scenarios=secondary,
                confidence=0.75,
                language=language,
                framework=framework,
                reasoning="存在 workspaces / lerna.json / nx.json / pnpm-workspace.yaml",
            )

        # 10. infra
        has_tf = any(f.endswith(".tf") for f in signals.files)
        has_k8s = self.repo_root_has_dir(signals, "k8s") or self.repo_root_has_dir(signals, "kubernetes")
        if has_tf or has_k8s:
            return ProjectProfile(
                primary_scenario=ScenarioType.INFRA,
                secondary_scenarios=secondary,
                confidence=0.75,
                language=language,
                framework=framework,
                reasoning=("检测到 .tf 文件" if has_tf else "") + (" + " if has_tf and has_k8s else "") + ("k8s/ 目录" if has_k8s else ""),
            )

        # 11. generic
        return ProjectProfile(
            primary_scenario=ScenarioType.GENERIC,
            secondary_scenarios=secondary,
            confidence=0.5,
            language=language,
            framework=framework,
            reasoning="未匹配到特定场景，使用通用配置",
        )

    # ---------- 工具方法 ----------

    @staticmethod
    def _detect_language(signals: ProjectSignals) -> str:
        if not signals.file_extensions:
            return "unknown"
        ranked = sorted(
            (
                (LANG_BY_EXT[ext], cnt)
                for ext, cnt in signals.file_extensions.items()
                if ext in LANG_BY_EXT
            ),
            key=lambda x: -x[1],
        )
        if not ranked:
            return "unknown"
        # 合并相同语言计数
        merged: dict[str, int] = {}
        for lang, cnt in ranked:
            merged[lang] = merged.get(lang, 0) + cnt
        return max(merged.items(), key=lambda x: x[1])[0]

    @staticmethod
    def repo_has_setup_py(signals: ProjectSignals) -> bool:
        return "setup.py" in {f.lower() for f in signals.files}

    @staticmethod
    def repo_root_has_dir(signals: ProjectSignals, name: str) -> bool:
        prefix = name.lower() + "/"
        return any(f.lower().startswith(prefix) for f in signals.files)
