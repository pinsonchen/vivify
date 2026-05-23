"""项目扫描器：os.walk + 文本解析，输出 ``ProjectSignals``。"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

IGNORE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".vivify",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "target",
    "out",
}

CODE_EXTS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".m",
    ".mm",
    ".rb",
    ".php",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".scala",
    ".dart",
    ".lua",
    ".sh",
    ".ps1",
}

URL_RE = re.compile(r"https?://[^\s)\]>\"]+")


@dataclass
class ProjectSignals:
    files: list[str] = field(default_factory=list)
    file_extensions: Counter = field(default_factory=Counter)
    total_files: int = 0
    total_lines: int = 0

    # 包管理
    has_package_json: bool = False
    has_pyproject_toml: bool = False
    has_requirements_txt: bool = False
    has_cargo_toml: bool = False
    has_go_mod: bool = False
    has_gemfile: bool = False
    has_pom_xml: bool = False

    # 框架
    detected_frameworks: list[str] = field(default_factory=list)

    # CI/CD
    ci_provider: str | None = None
    ci_config_path: str | None = None

    # 部署
    has_dockerfile: bool = False
    has_docker_compose: bool = False
    deploy_configs: list[str] = field(default_factory=list)

    # 文档
    readme_content: str | None = None
    readme_urls: list[str] = field(default_factory=list)

    # Git
    git_remote_url: str | None = None
    default_branch: str = "main"

    # 项目描述
    project_name: str | None = None
    project_description: str | None = None

    # 测试
    test_dirs: list[str] = field(default_factory=list)
    test_framework: str | None = None
    test_command: str | None = None

    # 运行
    scripts: dict[str, str] = field(default_factory=dict)
    entry_points: list[str] = field(default_factory=list)

    # 内部缓存
    _dependencies: set[str] = field(default_factory=set)


class Scanner:
    """扫描项目目录，收集分类所需的信号。"""

    MAX_DEPTH = 5
    MAX_FILES = 10000

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root).resolve()

    # ---------- 主入口 ----------

    def scan(self) -> ProjectSignals:
        signals = ProjectSignals()
        self._scan_files(signals)
        self._scan_readme(signals)
        self._scan_package_json(signals)
        self._scan_pyproject_toml(signals)
        self._scan_ci(signals)
        self._scan_git(signals)
        self._detect_frameworks(signals)
        self._detect_tests(signals)
        self._detect_deploy(signals)
        return signals

    # ---------- 文件遍历 ----------

    def _scan_files(self, signals: ProjectSignals) -> None:
        root = self.repo_root
        root_str = str(root)
        for dirpath, dirnames, filenames in os.walk(root):
            # 计算深度
            rel = os.path.relpath(dirpath, root_str)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth > self.MAX_DEPTH:
                dirnames[:] = []
                continue
            # 忽略目录
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".") or d in {".github", ".gitlab"}]

            for name in filenames:
                if signals.total_files >= self.MAX_FILES:
                    return
                full_path = os.path.join(dirpath, name)
                rel_path = os.path.relpath(full_path, root_str)
                signals.files.append(rel_path)
                signals.total_files += 1
                ext = os.path.splitext(name)[1].lower()
                if ext:
                    signals.file_extensions[ext] += 1

                lower = name.lower()
                if lower == "package.json":
                    signals.has_package_json = True
                elif lower == "pyproject.toml":
                    signals.has_pyproject_toml = True
                elif lower == "requirements.txt":
                    signals.has_requirements_txt = True
                elif lower == "cargo.toml":
                    signals.has_cargo_toml = True
                elif lower == "go.mod":
                    signals.has_go_mod = True
                elif lower == "gemfile":
                    signals.has_gemfile = True
                elif lower == "pom.xml":
                    signals.has_pom_xml = True
                elif lower == "dockerfile":
                    signals.has_dockerfile = True
                elif lower in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
                    signals.has_docker_compose = True

    # ---------- README ----------

    def _scan_readme(self, signals: ProjectSignals) -> None:
        for name in ("README.md", "README.MD", "README.rst", "README.txt", "README"):
            p = self.repo_root / name
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                signals.readme_content = content
                signals.readme_urls = list(dict.fromkeys(URL_RE.findall(content)))
                # 第一行以 # 开头，作为项目标题
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        title = stripped.lstrip("#").strip()
                        if title and not signals.project_name:
                            signals.project_name = title
                        break
                # 提取首个非空、非标题、非徽章段落作为描述
                if not signals.project_description:
                    for line in content.splitlines():
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#") or stripped.startswith("!["):
                            continue
                        if stripped.startswith("[") and "]" in stripped:
                            continue
                        signals.project_description = stripped[:200]
                        break
                return

    # ---------- package.json ----------

    def _scan_package_json(self, signals: ProjectSignals) -> None:
        p = self.repo_root / "package.json"
        if not p.is_file():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            if not signals.project_name and isinstance(data.get("name"), str):
                signals.project_name = data["name"]
            if not signals.project_description and isinstance(data.get("description"), str):
                signals.project_description = data["description"]
            scripts = data.get("scripts")
            if isinstance(scripts, dict):
                signals.scripts.update({k: str(v) for k, v in scripts.items() if isinstance(k, str)})
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                deps = data.get(key)
                if isinstance(deps, dict):
                    for dep in deps.keys():
                        if isinstance(dep, str):
                            signals._dependencies.add(dep.lower())
            if isinstance(data.get("workspaces"), (list, dict)):
                signals._dependencies.add("__workspaces__")

    # ---------- pyproject.toml ----------

    def _scan_pyproject_toml(self, signals: ProjectSignals) -> None:
        p = self.repo_root / "pyproject.toml"
        if not p.is_file():
            return
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        data: dict | None = None
        try:
            import tomllib  # type: ignore[import-not-found]

            data = tomllib.loads(text)
        except Exception:
            try:
                import tomli  # type: ignore[import-not-found]

                data = tomli.loads(text)
            except Exception:
                data = None

        if isinstance(data, dict):
            project = data.get("project") if isinstance(data.get("project"), dict) else None
            if project:
                if not signals.project_name and isinstance(project.get("name"), str):
                    signals.project_name = project["name"]
                if not signals.project_description and isinstance(project.get("description"), str):
                    signals.project_description = project["description"]
                deps = project.get("dependencies")
                if isinstance(deps, list):
                    for dep in deps:
                        if isinstance(dep, str):
                            name = re.split(r"[<>=!~;\s]", dep, maxsplit=1)[0].strip().lower()
                            if name:
                                signals._dependencies.add(name)
                scripts = project.get("scripts")
                if isinstance(scripts, dict):
                    for k in scripts.keys():
                        if isinstance(k, str):
                            signals.entry_points.append(k)
            return

        # tomllib 不可用时的正则 fallback
        m = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
        if m and not signals.project_name:
            signals.project_name = m.group(1)
        m = re.search(r'(?m)^\s*description\s*=\s*"([^"]+)"', text)
        if m and not signals.project_description:
            signals.project_description = m.group(1)
        m = re.search(r"(?ms)^\s*dependencies\s*=\s*\[(.*?)\]", text)
        if m:
            for raw in re.findall(r'"([^"]+)"', m.group(1)):
                name = re.split(r"[<>=!~;\s]", raw, maxsplit=1)[0].strip().lower()
                if name:
                    signals._dependencies.add(name)
        m = re.search(r"(?ms)\[project\.scripts\](.*?)(\n\[|\Z)", text)
        if m:
            for k in re.findall(r"(?m)^\s*([A-Za-z0-9_\-]+)\s*=", m.group(1)):
                signals.entry_points.append(k)

    # ---------- CI ----------

    def _scan_ci(self, signals: ProjectSignals) -> None:
        gh = self.repo_root / ".github" / "workflows"
        if gh.is_dir() and any(gh.iterdir()):
            signals.ci_provider = "github-actions"
            signals.ci_config_path = ".github/workflows"
            return
        for fname, provider in (
            (".gitlab-ci.yml", "gitlab-ci"),
            ("Jenkinsfile", "jenkins"),
            (".circleci/config.yml", "circleci"),
            (".travis.yml", "travis"),
            ("azure-pipelines.yml", "azure-pipelines"),
        ):
            p = self.repo_root / fname
            if p.exists():
                signals.ci_provider = provider
                signals.ci_config_path = fname
                return

    # ---------- Git ----------

    def _scan_git(self, signals: ProjectSignals) -> None:
        if not (self.repo_root / ".git").exists():
            return
        try:
            out = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if out.returncode == 0:
                signals.git_remote_url = out.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            out = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if out.returncode == 0 and out.stdout.strip():
                signals.default_branch = out.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass

    # ---------- 框架 ----------

    def _detect_frameworks(self, signals: ProjectSignals) -> None:
        deps = signals._dependencies
        marks: list[tuple[str, bool]] = [
            ("react", "react" in deps),
            ("vue", "vue" in deps or "@vue/cli-service" in deps),
            ("next", "next" in deps),
            ("nuxt", "nuxt" in deps or "nuxt3" in deps),
            ("angular", "@angular/core" in deps),
            ("svelte", "svelte" in deps or "@sveltejs/kit" in deps),
            ("astro", "astro" in deps),
            ("vite", "vite" in deps),
            ("express", "express" in deps),
            ("nestjs", "@nestjs/core" in deps),
            ("koa", "koa" in deps),
            ("fastify", "fastify" in deps),
            ("django", "django" in deps),
            ("flask", "flask" in deps),
            ("fastapi", "fastapi" in deps),
            ("starlette", "starlette" in deps),
            ("tornado", "tornado" in deps),
            ("spring", "spring-boot-starter" in deps or any(d.startswith("org.springframework") for d in deps)),
            ("rails", "rails" in deps),
            ("laravel", "laravel" in deps),
        ]
        for name, hit in marks:
            if hit:
                signals.detected_frameworks.append(name)

        # 文件名兜底
        files_lower = {f.lower() for f in signals.files}
        if "next.config.js" in files_lower or "next.config.mjs" in files_lower or "next.config.ts" in files_lower:
            if "next" not in signals.detected_frameworks:
                signals.detected_frameworks.append("next")
        if "vite.config.js" in files_lower or "vite.config.ts" in files_lower:
            if "vite" not in signals.detected_frameworks:
                signals.detected_frameworks.append("vite")
        if "manage.py" in files_lower and "django" not in signals.detected_frameworks:
            signals.detected_frameworks.append("django")

    # ---------- 测试 ----------

    def _detect_tests(self, signals: ProjectSignals) -> None:
        for cand in ("tests", "test", "__tests__", "spec", "specs"):
            p = self.repo_root / cand
            if p.is_dir():
                signals.test_dirs.append(cand)

        deps = signals._dependencies
        if "pytest" in deps:
            signals.test_framework = "pytest"
        elif "unittest" in deps or any(f.startswith("tests/") and f.endswith(".py") for f in signals.files):
            signals.test_framework = signals.test_framework or ("pytest" if signals.has_pyproject_toml else "unittest")
        if "jest" in deps:
            signals.test_framework = signals.test_framework or "jest"
        if "vitest" in deps:
            signals.test_framework = signals.test_framework or "vitest"
        if "mocha" in deps:
            signals.test_framework = signals.test_framework or "mocha"

        if "test" in signals.scripts:
            signals.test_command = "npm test"
        elif signals.test_framework == "pytest":
            signals.test_command = "pytest"
        elif signals.test_framework in {"jest", "vitest", "mocha"}:
            signals.test_command = f"npx {signals.test_framework}"
        elif signals.has_go_mod:
            signals.test_command = "go test ./..."
        elif signals.has_cargo_toml:
            signals.test_command = "cargo test"

    # ---------- 部署 ----------

    def _detect_deploy(self, signals: ProjectSignals) -> None:
        candidates = [
            "vercel.json",
            "netlify.toml",
            "CNAME",
            "wrangler.toml",
            "fly.toml",
            "render.yaml",
            "app.yaml",
            "Procfile",
            ".github/workflows",
        ]
        for c in candidates:
            if (self.repo_root / c).exists():
                signals.deploy_configs.append(c)
        if signals.has_dockerfile:
            signals.deploy_configs.append("Dockerfile")
        if signals.has_docker_compose:
            signals.deploy_configs.append("docker-compose")
