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
