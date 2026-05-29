"""场景化配置项发现器。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .classifier import ProjectProfile, ScenarioType  # noqa: F401
from .scanner import ProjectSignals


def _fill_question(
    questions: list["ConfigQuestion"], key: str, value: str, source: str
) -> None:
    """如果问题尚未设置默认值，则填充。"""
    for q in questions:
        if q.key == key and not q.default:
            q.default = value
            q.source = source
            break


def _has_value(questions: list["ConfigQuestion"], key: str) -> bool:
    """检查某个问题是否已经有默认值。"""
    for q in questions:
        if q.key == key and q.default:
            return True
    return False


@dataclass
class ConfigQuestion:
    key: str
    label: str
    hint: str
    required: bool = True
    default: str | None = None
    source: str | None = None
    options: list[str] | None = None


# 场景 → 启用的探针
SCENARIO_PROBES: dict[str, list[str]] = {
    "docs-only": ["doc_staleness", "github_issue_backlog", "stale_branches", "secrets_scan", "site_health"],
    "static-site": ["doc_staleness", "github_issue_backlog", "stale_branches", "secrets_scan", "repo_size", "site_health"],
    "web-app": [
        "ci_status",
        "dependency_vulnerabilities",
        "lint_typecheck",
        "test_coverage",
        "build_duration",
        "github_issue_backlog",
        "secrets_scan",
        "site_health",
    ],
    "api-service": [
        "ci_status",
        "dependency_vulnerabilities",
        "lint_typecheck",
        "test_coverage",
        "error_log_patterns",
        "build_duration",
        "github_issue_backlog",
        "secrets_scan",
        "site_health",
    ],
    "python-package": [
        "ci_status",
        "dependency_vulnerabilities",
        "lint_typecheck",
        "test_coverage",
        "dead_code",
        "doc_staleness",
        "secrets_scan",
    ],
    "cli-tool": [
        "ci_status",
        "dependency_vulnerabilities",
        "lint_typecheck",
        "test_coverage",
        "dead_code",
        "doc_staleness",
        "secrets_scan",
    ],
    "mobile-app": [
        "ci_status",
        "dependency_vulnerabilities",
        "test_coverage",
        "github_issue_backlog",
        "secrets_scan",
    ],
    "monorepo": [
        "ci_status",
        "dependency_vulnerabilities",
        "lint_typecheck",
        "test_coverage",
        "build_duration",
        "github_issue_backlog",
        "stale_branches",
        "secrets_scan",
    ],
    "infra": ["ci_status", "secrets_scan", "stale_branches", "github_issue_backlog"],
    "generic": [
        "ci_status",
        "dependency_vulnerabilities",
        "lint_typecheck",
        "test_coverage",
        "github_issue_backlog",
        "stale_branches",
        "secrets_scan",
    ],
}

# 场景 → 启用的修复器
SCENARIO_FIXERS: dict[str, list[str]] = {
    "docs-only": ["doc_link_check", "stale_branch_prune"],
    "static-site": ["doc_link_check", "stale_branch_prune"],
    "web-app": ["dependency_bump", "lint_autofix", "format_autofix", "test_flake_retry", "stale_branch_prune"],
    "api-service": ["dependency_bump", "lint_autofix", "format_autofix", "test_flake_retry", "stale_branch_prune"],
    "python-package": ["dependency_bump", "lint_autofix", "format_autofix", "test_flake_retry", "stale_branch_prune"],
    "cli-tool": ["dependency_bump", "lint_autofix", "format_autofix", "test_flake_retry", "stale_branch_prune"],
    "mobile-app": ["dependency_bump", "stale_branch_prune"],
    "monorepo": ["dependency_bump", "lint_autofix", "format_autofix", "test_flake_retry", "stale_branch_prune"],
    "infra": ["stale_branch_prune"],
    "generic": ["dependency_bump", "lint_autofix", "format_autofix", "stale_branch_prune"],
}


_SCENARIOS_WITH_DEPLOY_URL = {"docs-only", "static-site", "web-app", "api-service"}
_SCENARIOS_WITH_BUILD_CMD = {"web-app", "api-service"}
_SCENARIOS_WITH_TEST_CMD = {"web-app", "api-service", "python-package", "cli-tool", "monorepo"}
_SCENARIOS_WITH_LINT_CMD = {"web-app", "api-service", "python-package", "cli-tool", "monorepo"}


class Configurator:
    """根据项目场景确定所需配置项，并尝试自动填充。"""

    def required_config(self, profile: ProjectProfile) -> list[ConfigQuestion]:
        """返回该场景需要收集的配置问题。"""
        scenario = profile.primary_scenario.value
        questions: list[ConfigQuestion] = [
            ConfigQuestion(
                key="project.name",
                label="项目名称",
                hint="用于报告和 PR 描述",
                required=True,
            ),
            ConfigQuestion(
                key="project.description",
                label="项目简介",
                hint="一两句话描述这个项目",
                required=False,
            ),
        ]

        if scenario in _SCENARIOS_WITH_DEPLOY_URL:
            questions.append(
                ConfigQuestion(
                    key="deploy.url",
                    label="部署地址 / 站点 URL",
                    hint="例如 https://example.com，留空表示无在线部署",
                    required=False,
                )
            )

        if scenario == "api-service":
            questions.append(
                ConfigQuestion(
                    key="deploy.health_endpoint",
                    label="健康检查端点",
                    hint="例如 /health 或 /api/ping",
                    required=False,
                    default="/health",
                )
            )

        if scenario in _SCENARIOS_WITH_TEST_CMD:
            questions.append(
                ConfigQuestion(
                    key="commands.test",
                    label="测试命令",
                    hint="例如 pytest / npm test / go test ./...",
                    required=False,
                )
            )

        if scenario in _SCENARIOS_WITH_BUILD_CMD:
            questions.append(
                ConfigQuestion(
                    key="commands.build",
                    label="构建命令",
                    hint="例如 npm run build / make build",
                    required=False,
                )
            )

        if scenario in _SCENARIOS_WITH_LINT_CMD:
            questions.append(
                ConfigQuestion(
                    key="commands.lint",
                    label="Lint 命令",
                    hint="例如 ruff check . / eslint src/",
                    required=False,
                )
            )
            questions.append(
                ConfigQuestion(
                    key="commands.typecheck",
                    label="类型检查命令",
                    hint="例如 mypy . / tsc --noEmit",
                    required=False,
                )
            )

        return questions

    def auto_discover(
        self, signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> list[ConfigQuestion]:
        """尝试自动从项目信号中填充默认值。返回更新后的 questions。"""
        for q in questions:
            if q.default is not None:
                continue
    
            if q.key == "project.name":
                if signals.project_name:
                    q.default = signals.project_name.strip()
                    q.source = "auto"
    
            elif q.key == "project.description":
                if signals.project_description:
                    q.default = signals.project_description.strip()
                    q.source = "auto"
    
            elif q.key == "deploy.url":
                url = self._pick_deploy_url(signals)
                if url:
                    q.default = url
                    q.source = "readme"
    
            elif q.key == "commands.test":
                cmd = signals.test_command or signals.scripts.get("test")
                if cmd:
                    if cmd in signals.scripts.values():
                        # scripts 中的值需要前缀
                        for k, v in signals.scripts.items():
                            if v == cmd and k == "test":
                                cmd = "npm test"
                                break
                    q.default = cmd
                    q.source = "auto"
    
            elif q.key == "commands.build":
                if "build" in signals.scripts:
                    q.default = "npm run build"
                    q.source = "auto"
    
            # health_endpoint：保持 default（已有 /health 兑底）
    
        # ── 附加信号源：基于项目文件的 best-effort 推断 ──
        if signals.project_root is not None:
            self._discover_from_pyproject(signals, questions)
            self._discover_from_package_json(signals, questions)
            self._discover_from_workflows(signals, questions)
            self._discover_from_makefile(signals, questions)
            # ── 新增信号源 ──
            self._discover_from_dockerfile(signals, questions)
            self._discover_from_docker_compose(signals, questions)
            self._discover_from_ci_workflows_enhanced(signals, questions)
            self._discover_from_readme(signals, questions)
            self._discover_from_makefile_full(signals, questions)
            self._discover_from_runtime_versions(signals, questions)
    
        return questions
    
    # ---------- 附加发现源 ----------
    
    @staticmethod
    def _discover_from_pyproject(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 pyproject.toml 提取 description。"""
        try:
            assert signals.project_root is not None
            pyproject = signals.project_root / "pyproject.toml"
            if not pyproject.exists():
                return
            try:
                import tomllib  # Python 3.11+
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[import-not-found,no-redef]
                except ImportError:
                    return
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            project = data.get("project", {}) if isinstance(data, dict) else {}
            desc = project.get("description", "") if isinstance(project, dict) else ""
            if desc:
                _fill_question(questions, "project.description", desc, "pyproject.toml")
        except Exception:
            pass
    
    @staticmethod
    def _discover_from_package_json(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 package.json 提取 description。"""
        try:
            import json
    
            assert signals.project_root is not None
            package_json = signals.project_root / "package.json"
            if not package_json.exists():
                return
            pkg = json.loads(package_json.read_text(encoding="utf-8"))
            desc = pkg.get("description", "") if isinstance(pkg, dict) else ""
            if desc:
                _fill_question(questions, "project.description", desc, "package.json")
        except Exception:
            pass
    
    @staticmethod
    def _discover_from_workflows(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 .github/workflows 推断 deploy.method 与 commands.test。"""
        try:
            assert signals.project_root is not None
            workflows_dir = signals.project_root / ".github" / "workflows"
            if not workflows_dir.is_dir():
                return
            workflow_files = list(workflows_dir.glob("*.yml")) + list(
                workflows_dir.glob("*.yaml")
            )
    
            # 1) deploy.method 推断（遇到首个命中即返回）
            for wf in workflow_files:
                try:
                    content_lower = wf.read_text(encoding="utf-8").lower()
                except OSError:
                    continue
                source = f".github/workflows/{wf.name}"
                if "pages" in content_lower and "deploy" in content_lower:
                    _fill_question(questions, "deploy.method", "github-pages", source)
                    break
                if "ssh" in content_lower and "deploy" in content_lower:
                    _fill_question(questions, "deploy.method", "ssh", source)
                    break
                if "vercel" in content_lower:
                    _fill_question(questions, "deploy.method", "vercel", source)
                    break
    
            # 2) commands.test 推断
            for wf in workflow_files:
                try:
                    content = wf.read_text(encoding="utf-8")
                except OSError:
                    continue
                source = f".github/workflows/{wf.name}"
                if "pytest" in content and not _has_value(questions, "commands.test"):
                    _fill_question(
                        questions,
                        "commands.test",
                        "python -m pytest --tb=short -q",
                        source,
                    )
                elif "npm test" in content and not _has_value(questions, "commands.test"):
                    _fill_question(questions, "commands.test", "npm test", source)
        except Exception:
            pass
    
    @staticmethod
    def _discover_from_makefile(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 Makefile 提取 test/build/lint 命令。"""
        try:
            import re
    
            assert signals.project_root is not None
            makefile = signals.project_root / "Makefile"
            if not makefile.exists():
                return
            content = makefile.read_text(encoding="utf-8")
            for target in ("test", "lint", "build"):
                match = re.search(
                    rf"^{target}\s*:.*\n\t(.+)", content, re.MULTILINE
                )
                if match:
                    _fill_question(
                        questions,
                        f"commands.{target}",
                        f"make {target}",
                        "Makefile",
                    )
        except Exception:
            pass

    # ---------- 新增发现源 ----------

    @staticmethod
    def _discover_from_dockerfile(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 Dockerfile 推断 deploy.method 和 health_endpoint。"""
        try:
            import re

            assert signals.project_root is not None
            dockerfile = signals.project_root / "Dockerfile"
            if not dockerfile.exists():
                return
            content = dockerfile.read_text(encoding="utf-8", errors="ignore")

            # deploy.method → docker
            _fill_question(questions, "deploy.method", "docker", "Dockerfile")

            # EXPOSE → health_endpoint 端口
            expose_match = re.findall(r"(?im)^EXPOSE\s+(\d+)", content)
            if expose_match:
                port = expose_match[0]
                endpoint = f"http://localhost:{port}/health"
                _fill_question(questions, "deploy.health_endpoint", endpoint, "Dockerfile")
        except Exception:
            pass

    @staticmethod
    def _discover_from_docker_compose(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 docker-compose.yml 推断 deploy.method 和 health_endpoint。"""
        try:
            import re

            assert signals.project_root is not None
            compose_file = None
            for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
                candidate = signals.project_root / name
                if candidate.exists():
                    compose_file = candidate
                    break
            if compose_file is None:
                return
            content = compose_file.read_text(encoding="utf-8", errors="ignore")
            source = compose_file.name

            # deploy.method → docker-compose
            _fill_question(questions, "deploy.method", "docker-compose", source)

            # 解析 ports 映射（简易 regex，匹配 "HOST:CONTAINER" 或 "PORT"）
            port_matches = re.findall(r"[\"']?(\d+):(\d+)[\"']?", content)
            if port_matches:
                host_port = port_matches[0][0]
                endpoint = f"http://localhost:{host_port}/health"
                _fill_question(questions, "deploy.health_endpoint", endpoint, source)
        except Exception:
            pass

    @staticmethod
    def _discover_from_ci_workflows_enhanced(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 .github/workflows 增强提取 lint/typecheck/build 命令。"""
        try:
            import re

            assert signals.project_root is not None
            workflows_dir = signals.project_root / ".github" / "workflows"
            if not workflows_dir.is_dir():
                return
            workflow_files = list(workflows_dir.glob("*.yml")) + list(
                workflows_dir.glob("*.yaml")
            )

            # 命令模式 → 对应 question key
            lint_patterns = [
                (r"ruff\s+check", "ruff check ."),
                (r"flake8", "flake8"),
                (r"pylint", "pylint"),
                (r"eslint", "npx eslint ."),
                (r"biome\s+(lint|check)", "npx biome check ."),
            ]
            typecheck_patterns = [
                (r"mypy", "mypy ."),
                (r"pyright", "pyright"),
                (r"tsc\b.*--noEmit", "tsc --noEmit"),
                (r"tsc\b", "tsc --noEmit"),
            ]
            build_patterns = [
                (r"npm\s+run\s+build", "npm run build"),
                (r"yarn\s+build", "yarn build"),
                (r"pnpm\s+(?:run\s+)?build", "pnpm run build"),
                (r"cargo\s+build", "cargo build"),
                (r"go\s+build", "go build ./..."),
            ]

            for wf in workflow_files:
                try:
                    content = wf.read_text(encoding="utf-8")
                except OSError:
                    continue
                source = f".github/workflows/{wf.name}"

                # lint
                if not _has_value(questions, "commands.lint"):
                    for pattern, cmd in lint_patterns:
                        if re.search(pattern, content):
                            _fill_question(questions, "commands.lint", cmd, source)
                            break

                # typecheck
                if not _has_value(questions, "commands.typecheck"):
                    for pattern, cmd in typecheck_patterns:
                        if re.search(pattern, content):
                            _fill_question(questions, "commands.typecheck", cmd, source)
                            break

                # build
                if not _has_value(questions, "commands.build"):
                    for pattern, cmd in build_patterns:
                        if re.search(pattern, content):
                            _fill_question(questions, "commands.build", cmd, source)
                            break
        except Exception:
            pass

    @staticmethod
    def _discover_from_readme(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 README 提取 project.description 和 deploy.health_endpoint。"""
        try:
            import re

            assert signals.project_root is not None
            readme_path = None
            for name in ("README.md", "README.MD", "README.rst", "README.txt", "README"):
                candidate = signals.project_root / name
                if candidate.is_file():
                    readme_path = candidate
                    break
            if readme_path is None:
                return
            content = readme_path.read_text(encoding="utf-8", errors="ignore")

            # 提取项目描述（首段非标题非徽章文字）
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or stripped.startswith("!["):
                    continue
                if stripped.startswith("[") and "]" in stripped:
                    continue
                _fill_question(questions, "project.description", stripped[:200], "README.md")
                break

            # 提取 health endpoint 模式
            health_match = re.search(
                r"(https?://localhost:\d+/[\w/\-]*health[\w/\-]*)", content, re.IGNORECASE
            )
            if health_match:
                _fill_question(questions, "deploy.health_endpoint", health_match.group(1), "README.md")
            else:
                # 尝试匹配 /health 或 /api/health 模式
                endpoint_match = re.search(
                    r"(?:GET|POST|endpoint|路由|route)\s*[:`]?\s*(/[\w/\-]*health[\w/\-]*)",
                    content,
                    re.IGNORECASE,
                )
                if endpoint_match:
                    _fill_question(questions, "deploy.health_endpoint", endpoint_match.group(1), "README.md")
        except Exception:
            pass

    @staticmethod
    def _discover_from_makefile_full(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 Makefile 增强解析：lint / typecheck / format / deploy targets。"""
        try:
            import re

            assert signals.project_root is not None
            makefile = signals.project_root / "Makefile"
            if not makefile.exists():
                return
            content = makefile.read_text(encoding="utf-8", errors="ignore")

            # 额外 targets（基础 test/lint/build 已由 _discover_from_makefile 处理）
            extra_targets = {
                "typecheck": "commands.typecheck",
                "type-check": "commands.typecheck",
                "check": "commands.lint",
                "format": "commands.lint",  # fallback: format → lint
                "fmt": "commands.lint",
            }
            for target, key in extra_targets.items():
                match = re.search(
                    rf"^{re.escape(target)}\s*:.*\n\t(.+)", content, re.MULTILINE
                )
                if match and not _has_value(questions, key):
                    _fill_question(questions, key, f"make {target}", "Makefile")

            # deploy target → deploy.method
            deploy_match = re.search(
                r"^deploy\s*:.*\n\t(.+)", content, re.MULTILINE
            )
            if deploy_match:
                _fill_question(questions, "deploy.method", "command", "Makefile")
        except Exception:
            pass

    @staticmethod
    def _discover_from_runtime_versions(
        signals: ProjectSignals, questions: list[ConfigQuestion]
    ) -> None:
        """从 .python-version / .nvmrc / .tool-versions 提取运行时版本信息。"""
        try:
            assert signals.project_root is not None
            root = signals.project_root

            # .python-version
            py_ver_file = root / ".python-version"
            if py_ver_file.is_file():
                ver = py_ver_file.read_text(encoding="utf-8").strip().splitlines()
                if ver:
                    _fill_question(
                        questions, "project.runtime_version",
                        f"python {ver[0]}", ".python-version"
                    )

            # .nvmrc
            nvmrc = root / ".nvmrc"
            if nvmrc.is_file():
                ver = nvmrc.read_text(encoding="utf-8").strip().splitlines()
                if ver:
                    _fill_question(
                        questions, "project.runtime_version",
                        f"node {ver[0]}", ".nvmrc"
                    )

            # .tool-versions (asdf format: "python 3.11.0\nnode 18.0.0")
            tool_versions = root / ".tool-versions"
            if tool_versions.is_file():
                content = tool_versions.read_text(encoding="utf-8").strip()
                if content:
                    # 取第一个有效行作为 runtime
                    for line in content.splitlines():
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#"):
                            _fill_question(
                                questions, "project.runtime_version",
                                stripped, ".tool-versions"
                            )
                            break
        except Exception:
            pass

    def get_probes(self, profile: ProjectProfile) -> list[str]:
        """返回该场景推荐的探针列表。"""
        scenario = profile.primary_scenario.value
        return list(SCENARIO_PROBES.get(scenario, SCENARIO_PROBES["generic"]))

    def get_fixers(self, profile: ProjectProfile) -> list[str]:
        """返回该场景推荐的修复器列表。"""
        scenario = profile.primary_scenario.value
        return list(SCENARIO_FIXERS.get(scenario, SCENARIO_FIXERS["generic"]))

    # ---------- 工具方法 ----------

    @staticmethod
    def _pick_deploy_url(signals: ProjectSignals) -> str | None:
        """从 README URL 中挑选最可能的部署 URL。

        策略：
        1. 排除 github.com / gitlab.com / 徽章服务等。
        2. 优先选择 URL 中包含项目名的。
        3. 否则取第一个候选。
        """
        if not signals.readme_urls:
            return None

        excluded_hosts = {
            "github.com",
            "www.github.com",
            "gitlab.com",
            "raw.githubusercontent.com",
            "img.shields.io",
            "shields.io",
            "badge.fury.io",
            "codecov.io",
            "travis-ci.com",
            "travis-ci.org",
            "circleci.com",
            "deepsource.io",
            "snyk.io",
            "npmjs.com",
            "www.npmjs.com",
            "pypi.org",
        }

        candidates: list[str] = []
        for raw in signals.readme_urls:
            url = raw.rstrip(").,;'\"")
            try:
                parsed = urlparse(url)
            except ValueError:
                continue
            if parsed.scheme not in {"http", "https"}:
                continue
            host = (parsed.netloc or "").lower()
            if not host or host in excluded_hosts:
                continue
            if any(host.endswith("." + h) or host == h for h in excluded_hosts):
                continue
            candidates.append(url)

        if not candidates:
            return None

        name = (signals.project_name or "").lower().strip()
        if name:
            slug = name.replace(" ", "-").replace("_", "-")
            for url in candidates:
                if slug and slug in url.lower():
                    return url

        return candidates[0]
