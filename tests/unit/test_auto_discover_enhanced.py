"""测试增强版 auto_discover 信号源。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vivify.intelligence.configurator import ConfigQuestion, Configurator, _fill_question, _has_value
from vivify.intelligence.scanner import ProjectSignals


@pytest.fixture
def configurator():
    return Configurator()


@pytest.fixture
def base_questions():
    """模拟一组常见的 ConfigQuestion。"""
    return [
        ConfigQuestion(key="project.name", label="项目名称", hint=""),
        ConfigQuestion(key="project.description", label="项目简介", hint="", required=False),
        ConfigQuestion(key="deploy.method", label="部署方式", hint="", required=False),
        ConfigQuestion(key="deploy.health_endpoint", label="健康检查", hint="", required=False),
        ConfigQuestion(key="commands.test", label="测试命令", hint="", required=False),
        ConfigQuestion(key="commands.build", label="构建命令", hint="", required=False),
        ConfigQuestion(key="commands.lint", label="Lint 命令", hint="", required=False),
        ConfigQuestion(key="commands.typecheck", label="类型检查", hint="", required=False),
        ConfigQuestion(key="project.runtime_version", label="运行时版本", hint="", required=False),
    ]


def _make_signals(tmp_path: Path) -> ProjectSignals:
    signals = ProjectSignals()
    signals.project_root = tmp_path
    return signals


# ─────────────────────────── Dockerfile ───────────────────────────


class TestDiscoverFromDockerfile:
    def test_basic_dockerfile(self, tmp_path, configurator, base_questions):
        """Dockerfile 存在时推断 deploy.method=docker。"""
        (tmp_path / "Dockerfile").write_text(
            "FROM python:3.11-slim\nEXPOSE 8000\nCMD [\"python\", \"app.py\"]\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_dockerfile(signals, base_questions)

        method_q = next(q for q in base_questions if q.key == "deploy.method")
        assert method_q.default == "docker"
        assert method_q.source == "Dockerfile"

        health_q = next(q for q in base_questions if q.key == "deploy.health_endpoint")
        assert health_q.default == "http://localhost:8000/health"

    def test_no_expose(self, tmp_path, configurator, base_questions):
        """Dockerfile 没有 EXPOSE 时只推断 deploy.method。"""
        (tmp_path / "Dockerfile").write_text("FROM node:18\nRUN npm install\n")
        signals = _make_signals(tmp_path)
        configurator._discover_from_dockerfile(signals, base_questions)

        method_q = next(q for q in base_questions if q.key == "deploy.method")
        assert method_q.default == "docker"

        health_q = next(q for q in base_questions if q.key == "deploy.health_endpoint")
        assert health_q.default is None

    def test_no_dockerfile(self, tmp_path, configurator, base_questions):
        """Dockerfile 不存在时无操作。"""
        signals = _make_signals(tmp_path)
        configurator._discover_from_dockerfile(signals, base_questions)

        method_q = next(q for q in base_questions if q.key == "deploy.method")
        assert method_q.default is None


# ─────────────────────────── docker-compose ───────────────────────────


class TestDiscoverFromDockerCompose:
    def test_compose_with_ports(self, tmp_path, configurator, base_questions):
        """docker-compose.yml 存在时推断 method 和端口。"""
        (tmp_path / "docker-compose.yml").write_text(
            "version: '3'\nservices:\n  web:\n    ports:\n      - '3000:8080'\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_docker_compose(signals, base_questions)

        method_q = next(q for q in base_questions if q.key == "deploy.method")
        assert method_q.default == "docker-compose"
        assert method_q.source == "docker-compose.yml"

        health_q = next(q for q in base_questions if q.key == "deploy.health_endpoint")
        assert health_q.default == "http://localhost:3000/health"

    def test_compose_yaml_variant(self, tmp_path, configurator, base_questions):
        """支持 compose.yaml 命名。"""
        (tmp_path / "compose.yaml").write_text(
            "services:\n  api:\n    ports:\n      - '5000:5000'\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_docker_compose(signals, base_questions)

        method_q = next(q for q in base_questions if q.key == "deploy.method")
        assert method_q.default == "docker-compose"

    def test_no_compose(self, tmp_path, configurator, base_questions):
        """docker-compose 不存在时无操作。"""
        signals = _make_signals(tmp_path)
        configurator._discover_from_docker_compose(signals, base_questions)

        method_q = next(q for q in base_questions if q.key == "deploy.method")
        assert method_q.default is None


# ─────────────────────────── CI Workflows Enhanced ───────────────────────────


class TestDiscoverFromCIWorkflowsEnhanced:
    def test_lint_ruff(self, tmp_path, configurator, base_questions):
        """CI 中有 ruff check → commands.lint。"""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "name: CI\non: push\njobs:\n  lint:\n    steps:\n      - run: ruff check .\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_ci_workflows_enhanced(signals, base_questions)

        lint_q = next(q for q in base_questions if q.key == "commands.lint")
        assert lint_q.default == "ruff check ."

    def test_typecheck_mypy(self, tmp_path, configurator, base_questions):
        """CI 中有 mypy → commands.typecheck。"""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "name: CI\njobs:\n  check:\n    steps:\n      - run: mypy src/\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_ci_workflows_enhanced(signals, base_questions)

        tc_q = next(q for q in base_questions if q.key == "commands.typecheck")
        assert tc_q.default == "mypy ."

    def test_build_npm(self, tmp_path, configurator, base_questions):
        """CI 中有 npm run build → commands.build。"""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "deploy.yml").write_text(
            "name: Deploy\njobs:\n  build:\n    steps:\n      - run: npm run build\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_ci_workflows_enhanced(signals, base_questions)

        build_q = next(q for q in base_questions if q.key == "commands.build")
        assert build_q.default == "npm run build"

    def test_eslint_detection(self, tmp_path, configurator, base_questions):
        """CI 中有 eslint → commands.lint。"""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "lint.yml").write_text(
            "name: Lint\njobs:\n  lint:\n    steps:\n      - run: eslint src/\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_ci_workflows_enhanced(signals, base_questions)

        lint_q = next(q for q in base_questions if q.key == "commands.lint")
        assert lint_q.default == "npx eslint ."

    def test_tsc_noEmit(self, tmp_path, configurator, base_questions):
        """CI 中有 tsc --noEmit → commands.typecheck。"""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "name: CI\njobs:\n  types:\n    steps:\n      - run: tsc --noEmit\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_ci_workflows_enhanced(signals, base_questions)

        tc_q = next(q for q in base_questions if q.key == "commands.typecheck")
        assert tc_q.default == "tsc --noEmit"

    def test_no_workflows_dir(self, tmp_path, configurator, base_questions):
        """没有 .github/workflows 目录时无操作。"""
        signals = _make_signals(tmp_path)
        configurator._discover_from_ci_workflows_enhanced(signals, base_questions)

        lint_q = next(q for q in base_questions if q.key == "commands.lint")
        assert lint_q.default is None

    def test_already_filled_no_override(self, tmp_path, configurator, base_questions):
        """已有默认值时不覆盖。"""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text("jobs:\n  lint:\n    steps:\n      - run: ruff check .\n")

        lint_q = next(q for q in base_questions if q.key == "commands.lint")
        lint_q.default = "existing"

        signals = _make_signals(tmp_path)
        configurator._discover_from_ci_workflows_enhanced(signals, base_questions)
        assert lint_q.default == "existing"


# ─────────────────────────── README ───────────────────────────


class TestDiscoverFromReadme:
    def test_description_from_readme(self, tmp_path, configurator, base_questions):
        """从 README 提取项目描述。"""
        (tmp_path / "README.md").write_text(
            "# My Project\n\nA fantastic tool for doing things.\n\n## Installation\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_readme(signals, base_questions)

        desc_q = next(q for q in base_questions if q.key == "project.description")
        assert desc_q.default == "A fantastic tool for doing things."

    def test_health_endpoint_from_readme(self, tmp_path, configurator, base_questions):
        """从 README 中的 URL 提取 health endpoint。"""
        (tmp_path / "README.md").write_text(
            "# API Server\n\nHealth check: http://localhost:8080/health\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_readme(signals, base_questions)

        health_q = next(q for q in base_questions if q.key == "deploy.health_endpoint")
        assert health_q.default == "http://localhost:8080/health"

    def test_endpoint_pattern_from_readme(self, tmp_path, configurator, base_questions):
        """从 README 中的路由描述提取 health endpoint。"""
        (tmp_path / "README.md").write_text(
            "# Service\n\nSome service for things.\n\nGET `/api/health` - health check\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_readme(signals, base_questions)

        health_q = next(q for q in base_questions if q.key == "deploy.health_endpoint")
        assert health_q.default == "/api/health"

    def test_no_readme(self, tmp_path, configurator, base_questions):
        """没有 README 时无操作。"""
        signals = _make_signals(tmp_path)
        configurator._discover_from_readme(signals, base_questions)

        desc_q = next(q for q in base_questions if q.key == "project.description")
        assert desc_q.default is None


# ─────────────────────────── Makefile Full ───────────────────────────


class TestDiscoverFromMakefileFull:
    def test_typecheck_target(self, tmp_path, configurator, base_questions):
        """Makefile 有 typecheck target。"""
        (tmp_path / "Makefile").write_text(
            "typecheck:\n\tmypy src/\n\nlint:\n\truff check .\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_makefile_full(signals, base_questions)

        tc_q = next(q for q in base_questions if q.key == "commands.typecheck")
        assert tc_q.default == "make typecheck"

    def test_deploy_target(self, tmp_path, configurator, base_questions):
        """Makefile 有 deploy target → deploy.method=command。"""
        (tmp_path / "Makefile").write_text(
            "deploy:\n\t./deploy.sh production\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_makefile_full(signals, base_questions)

        method_q = next(q for q in base_questions if q.key == "deploy.method")
        assert method_q.default == "command"

    def test_format_target_as_lint_fallback(self, tmp_path, configurator, base_questions):
        """format target 作为 lint 的 fallback。"""
        (tmp_path / "Makefile").write_text(
            "format:\n\tblack . && isort .\n"
        )
        signals = _make_signals(tmp_path)
        configurator._discover_from_makefile_full(signals, base_questions)

        lint_q = next(q for q in base_questions if q.key == "commands.lint")
        assert lint_q.default == "make format"

    def test_no_makefile(self, tmp_path, configurator, base_questions):
        """Makefile 不存在时无操作。"""
        signals = _make_signals(tmp_path)
        configurator._discover_from_makefile_full(signals, base_questions)

        tc_q = next(q for q in base_questions if q.key == "commands.typecheck")
        assert tc_q.default is None


# ─────────────────────────── Runtime Versions ───────────────────────────


class TestDiscoverFromRuntimeVersions:
    def test_python_version(self, tmp_path, configurator, base_questions):
        """.python-version → runtime version。"""
        (tmp_path / ".python-version").write_text("3.11.4\n")
        signals = _make_signals(tmp_path)
        configurator._discover_from_runtime_versions(signals, base_questions)

        rt_q = next(q for q in base_questions if q.key == "project.runtime_version")
        assert rt_q.default == "python 3.11.4"
        assert rt_q.source == ".python-version"

    def test_nvmrc(self, tmp_path, configurator, base_questions):
        """.nvmrc → runtime version。"""
        (tmp_path / ".nvmrc").write_text("18.17.0\n")
        signals = _make_signals(tmp_path)
        configurator._discover_from_runtime_versions(signals, base_questions)

        rt_q = next(q for q in base_questions if q.key == "project.runtime_version")
        assert rt_q.default == "node 18.17.0"

    def test_tool_versions(self, tmp_path, configurator, base_questions):
        """.tool-versions (asdf) → runtime version。"""
        (tmp_path / ".tool-versions").write_text("python 3.12.0\nnode 20.0.0\n")
        signals = _make_signals(tmp_path)
        configurator._discover_from_runtime_versions(signals, base_questions)

        rt_q = next(q for q in base_questions if q.key == "project.runtime_version")
        assert rt_q.default == "python 3.12.0"
        assert rt_q.source == ".tool-versions"

    def test_no_version_files(self, tmp_path, configurator, base_questions):
        """没有版本文件时无操作。"""
        signals = _make_signals(tmp_path)
        configurator._discover_from_runtime_versions(signals, base_questions)

        rt_q = next(q for q in base_questions if q.key == "project.runtime_version")
        assert rt_q.default is None

    def test_python_version_priority_over_nvmrc(self, tmp_path, configurator, base_questions):
        """.python-version 先被发现时优先。"""
        (tmp_path / ".python-version").write_text("3.11.0\n")
        (tmp_path / ".nvmrc").write_text("18.0.0\n")
        signals = _make_signals(tmp_path)
        configurator._discover_from_runtime_versions(signals, base_questions)

        rt_q = next(q for q in base_questions if q.key == "project.runtime_version")
        assert rt_q.default == "python 3.11.0"


# ─────────────────────────── 优先级 / 集成测试 ───────────────────────────


class TestDiscoveryPriority:
    def test_ci_takes_priority_over_makefile(self, tmp_path, configurator, base_questions):
        """CI 信号源先于 Makefile full 被处理，不会被覆盖。"""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text("jobs:\n  lint:\n    steps:\n      - run: ruff check .\n")
        (tmp_path / "Makefile").write_text("typecheck:\n\tmypy .\n\nformat:\n\tblack .\n")

        signals = _make_signals(tmp_path)
        # 模拟执行顺序：CI enhanced 先执行
        configurator._discover_from_ci_workflows_enhanced(signals, base_questions)
        configurator._discover_from_makefile_full(signals, base_questions)

        lint_q = next(q for q in base_questions if q.key == "commands.lint")
        assert lint_q.default == "ruff check ."  # CI 优先

    def test_dockerfile_priority_over_compose(self, tmp_path, configurator, base_questions):
        """Dockerfile 先处理，docker-compose 不会覆盖 deploy.method。"""
        (tmp_path / "Dockerfile").write_text("FROM python:3.11\nEXPOSE 8000\n")
        (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    ports:\n      - '3000:8000'\n")

        signals = _make_signals(tmp_path)
        configurator._discover_from_dockerfile(signals, base_questions)
        configurator._discover_from_docker_compose(signals, base_questions)

        method_q = next(q for q in base_questions if q.key == "deploy.method")
        assert method_q.default == "docker"  # Dockerfile 优先

    def test_full_auto_discover_integration(self, tmp_path, configurator):
        """完整 auto_discover 流程集成测试。"""
        from vivify.intelligence.classifier import ProjectProfile, ScenarioType

        # 准备项目结构
        (tmp_path / "Dockerfile").write_text("FROM node:18\nEXPOSE 3000\n")
        (tmp_path / ".python-version").write_text("3.11.0\n")
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "name: CI\njobs:\n  lint:\n    steps:\n      - run: eslint src/\n"
            "  test:\n    steps:\n      - run: npm test\n"
        )

        signals = _make_signals(tmp_path)
        signals.project_name = "test-project"

        profile = ProjectProfile(
            primary_scenario=ScenarioType.WEB_APP,
            secondary_scenarios=[],
            confidence=0.9,
            language="javascript",
            framework="node",
            reasoning="test",
        )
        questions = configurator.required_config(profile)
        result = configurator.auto_discover(signals, questions)

        # 验证部分字段被填充
        filled_keys = {q.key for q in result if q.default is not None}
        assert "project.name" in filled_keys
        assert "commands.lint" in filled_keys


# ─────────────────────────── Graceful failure ───────────────────────────


class TestGracefulFailure:
    def test_unreadable_dockerfile_no_crash(self, tmp_path, configurator, base_questions):
        """Dockerfile 不可读时不崩溃。"""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\n")
        df.chmod(0o000)

        signals = _make_signals(tmp_path)
        # 不应抛出异常
        configurator._discover_from_dockerfile(signals, base_questions)
        # 恢复权限以便 cleanup
        df.chmod(0o644)

    def test_malformed_compose_no_crash(self, tmp_path, configurator, base_questions):
        """docker-compose 格式异常时不崩溃。"""
        (tmp_path / "docker-compose.yml").write_text("{{{{invalid yaml content")
        signals = _make_signals(tmp_path)
        configurator._discover_from_docker_compose(signals, base_questions)

        # 即使内容无效，deploy.method 仍然可以从文件存在推断
        method_q = next(q for q in base_questions if q.key == "deploy.method")
        assert method_q.default == "docker-compose"

    def test_empty_python_version_no_crash(self, tmp_path, configurator, base_questions):
        """空 .python-version 不崩溃。"""
        (tmp_path / ".python-version").write_text("")
        signals = _make_signals(tmp_path)
        configurator._discover_from_runtime_versions(signals, base_questions)

        rt_q = next(q for q in base_questions if q.key == "project.runtime_version")
        assert rt_q.default is None
