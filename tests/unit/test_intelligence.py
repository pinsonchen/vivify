"""Unit tests for vivify.intelligence module."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from vivify.intelligence import Scanner, Classifier, Configurator, Interviewer, ScenarioType
from vivify.intelligence.scanner import ProjectSignals
from vivify.intelligence.configurator import ConfigQuestion, SCENARIO_PROBES, SCENARIO_FIXERS
from vivify.intelligence.goals_templates import render_goals
from vivify.intelligence.classifier import ProjectProfile


# ============================================================
# Scanner Tests
# ============================================================


class TestScanner:
    """扫描器测试。"""

    def test_scan_directory_with_files(self, tmp_path: Path) -> None:
        """测试扫描包含 README.md 和 index.html 的目录。"""
        (tmp_path / "README.md").write_text("# Test Project\n\nA test.", encoding="utf-8")
        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

        scanner = Scanner(tmp_path)
        signals = scanner.scan()

        assert signals.total_files >= 2
        assert ".md" in signals.file_extensions
        assert ".html" in signals.file_extensions
        assert any("README.md" in f for f in signals.files)
        assert any("index.html" in f for f in signals.files)

    def test_scan_empty_directory(self, tmp_path: Path) -> None:
        """测试空目录扫描不报错。"""
        scanner = Scanner(tmp_path)
        signals = scanner.scan()

        assert signals.total_files == 0
        assert signals.files == []
        assert len(signals.file_extensions) == 0

    def test_ignore_dirs_are_skipped(self, tmp_path: Path) -> None:
        """测试 IGNORE_DIRS 被正确跳过。"""
        # 创建会被忽略的目录
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "package.json").write_text("{}", encoding="utf-8")

        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "mod.cpython-310.pyc").write_text("", encoding="utf-8")

        venv_dir = tmp_path / "venv"
        venv_dir.mkdir()
        (venv_dir / "pyvenv.cfg").write_text("", encoding="utf-8")

        # 创建不被忽略的文件
        (tmp_path / "app.py").write_text("print('hello')", encoding="utf-8")

        scanner = Scanner(tmp_path)
        signals = scanner.scan()

        # 被忽略目录中的文件不应出现
        for f in signals.files:
            assert not f.startswith("node_modules/"), f"node_modules should be ignored: {f}"
            assert not f.startswith("__pycache__/"), f"__pycache__ should be ignored: {f}"
            assert not f.startswith("venv/"), f"venv should be ignored: {f}"

        # app.py 应该存在
        assert any("app.py" in f for f in signals.files)


# ============================================================
# Classifier Tests
# ============================================================


class TestClassifier:
    """分类器测试。"""

    def test_classify_docs_only(self) -> None:
        """构造 docs-only 项目 signals，验证返回 DOCS_ONLY。"""
        signals = ProjectSignals(
            files=["README.md", "guide.md", "api.md", "changelog.md", "faq.md",
                   "config.yml"],
            file_extensions=Counter({".md": 5, ".yml": 1}),
            total_files=6,
        )
        classifier = Classifier()
        profile = classifier.classify(signals)
        assert profile.primary_scenario == ScenarioType.DOCS_ONLY

    def test_classify_web_app(self) -> None:
        """构造 web-app 项目 signals，验证返回 WEB_APP。"""
        signals = ProjectSignals(
            files=["package.json", "src/App.tsx", "src/index.tsx"],
            file_extensions=Counter({".json": 1, ".tsx": 2}),
            total_files=3,
            has_package_json=True,
            detected_frameworks=["react"],
        )
        classifier = Classifier()
        profile = classifier.classify(signals)
        assert profile.primary_scenario == ScenarioType.WEB_APP

    def test_classify_static_site(self) -> None:
        """构造 static-site 项目 signals，验证返回 STATIC_SITE。"""
        signals = ProjectSignals(
            files=["index.html", "styles.css", "script.js"],
            file_extensions=Counter({".html": 1, ".css": 1, ".js": 1}),
            total_files=3,
            has_package_json=False,
        )
        classifier = Classifier()
        profile = classifier.classify(signals)
        assert profile.primary_scenario == ScenarioType.STATIC_SITE

    def test_classify_python_package(self) -> None:
        """构造 python-package 项目 signals，验证返回 PYTHON_PACKAGE。"""
        signals = ProjectSignals(
            files=["pyproject.toml", "src/__init__.py", "src/main.py", "tests/test_main.py"],
            file_extensions=Counter({".toml": 1, ".py": 3}),
            total_files=4,
            has_pyproject_toml=True,
        )
        classifier = Classifier()
        profile = classifier.classify(signals)
        assert profile.primary_scenario == ScenarioType.PYTHON_PACKAGE

    def test_classify_generic_fallback(self) -> None:
        """测试无特定标志时回退为 GENERIC。"""
        signals = ProjectSignals(
            files=["data.csv", "notes.txt", "image.png"],
            file_extensions=Counter({".csv": 1, ".txt": 1, ".png": 1}),
            total_files=3,
        )
        classifier = Classifier()
        profile = classifier.classify(signals)
        assert profile.primary_scenario == ScenarioType.GENERIC


# ============================================================
# Configurator Tests
# ============================================================


class TestConfigurator:
    """配置器测试。"""

    def test_required_config_docs_only_has_deploy_url(self) -> None:
        """测试 docs-only 场景返回包含 deploy_url 的问题。"""
        profile = ProjectProfile(
            primary_scenario=ScenarioType.DOCS_ONLY,
            secondary_scenarios=[],
            confidence=0.85,
            language="unknown",
            framework=None,
            reasoning="test",
        )
        configurator = Configurator()
        questions = configurator.required_config(profile)
        keys = [q.key for q in questions]
        assert "deploy.url" in keys

    def test_auto_discover_deploy_url(self) -> None:
        """测试 auto_discover 当 signals.readme_urls 包含有效 URL 时能填充 deploy_url。"""
        signals = ProjectSignals(
            project_name="mlive",
            readme_urls=["https://tools.pinsonbot.com/mlive/"],
        )
        questions = [
            ConfigQuestion(
                key="deploy.url",
                label="部署地址",
                hint="URL",
                required=False,
            ),
        ]
        configurator = Configurator()
        updated = configurator.auto_discover(signals, questions)
        deploy_q = next(q for q in updated if q.key == "deploy.url")
        assert deploy_q.default == "https://tools.pinsonbot.com/mlive/"
        assert deploy_q.source == "readme"

    def test_get_probes_returns_correct_mapping(self) -> None:
        """测试 get_probes 返回正确的场景映射。"""
        configurator = Configurator()
        for scenario_type in ScenarioType:
            profile = ProjectProfile(
                primary_scenario=scenario_type,
                secondary_scenarios=[],
                confidence=0.8,
                language="python",
                framework=None,
                reasoning="test",
            )
            probes = configurator.get_probes(profile)
            expected = SCENARIO_PROBES.get(scenario_type.value, SCENARIO_PROBES["generic"])
            assert probes == expected, f"Probes mismatch for {scenario_type.value}"

    def test_get_fixers_returns_correct_mapping(self) -> None:
        """测试 get_fixers 返回正确的场景映射。"""
        configurator = Configurator()
        for scenario_type in ScenarioType:
            profile = ProjectProfile(
                primary_scenario=scenario_type,
                secondary_scenarios=[],
                confidence=0.8,
                language="python",
                framework=None,
                reasoning="test",
            )
            fixers = configurator.get_fixers(profile)
            expected = SCENARIO_FIXERS.get(scenario_type.value, SCENARIO_FIXERS["generic"])
            assert fixers == expected, f"Fixers mismatch for {scenario_type.value}"


# ============================================================
# Interviewer Tests
# ============================================================


class TestInterviewer:
    """交互式问答测试。"""

    def test_non_interactive_uses_defaults(self) -> None:
        """测试 non_interactive=True 时不弹出输入，使用默认值。"""
        questions = [
            ConfigQuestion(key="project.name", label="Name", hint="", default="my-project"),
            ConfigQuestion(key="deploy.url", label="URL", hint="", required=False),
        ]
        interviewer = Interviewer()
        results = interviewer.conduct(questions, non_interactive=True)
        assert results["project.name"] == "my-project"
        # 无 default 的项在 non_interactive 下为空字符串
        assert results["deploy.url"] == ""

    def test_question_with_default_uses_default(self) -> None:
        """测试有 default 的 question 直接使用 default。"""
        questions = [
            ConfigQuestion(key="commands.test", label="Test cmd", hint="", default="pytest"),
            ConfigQuestion(key="deploy.health", label="Health", hint="", default="/health"),
        ]
        interviewer = Interviewer()
        results = interviewer.conduct(questions, non_interactive=True)
        assert results["commands.test"] == "pytest"
        assert results["deploy.health"] == "/health"


# ============================================================
# Goals Templates Tests
# ============================================================


class TestGoalsTemplates:
    """Goals 模板测试。"""

    def test_render_goals_all_scenarios_non_empty(self) -> None:
        """测试 render_goals 对各场景返回非空字符串。"""
        for scenario_type in ScenarioType:
            result = render_goals(scenario_type.value)
            assert result, f"render_goals returned empty for {scenario_type.value}"
            assert len(result) > 50, f"render_goals too short for {scenario_type.value}"

    def test_render_goals_has_yaml_front_matter(self) -> None:
        """测试返回的 GOALS 包含 YAML front matter。"""
        for scenario_type in ScenarioType:
            result = render_goals(scenario_type.value)
            assert result.startswith("---"), f"Missing YAML front matter start for {scenario_type.value}"
            # 确认有第二个 ---
            lines = result.split("\n")
            dashes = [i for i, line in enumerate(lines) if line.strip() == "---"]
            assert len(dashes) >= 2, f"Missing closing YAML front matter for {scenario_type.value}"

    def test_render_goals_unknown_scenario_fallback(self) -> None:
        """测试未知场景回退为 generic。"""
        result = render_goals("unknown-scenario-xyz")
        generic_result = render_goals("generic")
        assert result == generic_result
