"""``vivify init`` — scaffold a new repo for vivify use (with intelligent analysis)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from vivify.config.defaults import DEFAULT_GITIGNORE_ENTRIES
from vivify.intelligence import (
    Configurator,
    Interviewer,
    Scanner,
    ScenarioType,
)
from vivify.intelligence.ai_analyzer import AIAnalyzer
from vivify.intelligence.classifier import ProjectProfile
from vivify.intelligence.goals_templates import render_goals
from vivify.intelligence.wiki_generator import (
    DEFAULT_WIKI_DIR,
    WikiContext,
    generate_wiki,
    parse_wiki_metadata,
)
from vivify.knowledge.builder import KnowledgeBuilder


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help="Scaffold a repo for vivify use.")
    p.add_argument("--repo", default=".", help="Target repo path (defaults to cwd).")
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing .vivify.yml / GOALS.md.",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use defaults without prompting.",
    )
    p.add_argument(
        "--type",
        choices=[s.value for s in ScenarioType],
        default=None,
        help="Override detected project scenario type.",
    )
    p.add_argument("--qodercli-path", default="qodercli", help="qodercli 可执行文件路径")
    p.add_argument(
        "--template",
        choices=["quick", "full"],
        default="full",
        help="配置模板: quick(精简30行) / full(完整95行)",
    )
    p.set_defaults(func=run)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _copy_template(name: str, dest: Path, *, force: bool) -> bool:
    src = files("vivify.templates").joinpath(name)
    if dest.exists() and not force:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def _patch_gitignore(repo: Path) -> None:
    gi = repo / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    add = [e for e in DEFAULT_GITIGNORE_ENTRIES if e not in existing]
    if not add:
        return
    block = "\n# vivify\n" + "\n".join(add) + "\n"
    with gi.open("a", encoding="utf-8") as fh:
        fh.write(block)


def _write_user_dir_readmes(repo: Path) -> None:
    probes_dir = repo / ".vivify" / "probes"
    fixers_dir = repo / ".vivify" / "fixers"
    probes_dir.mkdir(parents=True, exist_ok=True)
    fixers_dir.mkdir(parents=True, exist_ok=True)
    (probes_dir / "README.md").write_text(
        "# User probes\n\nDrop `.yml` or `.py` probe definitions here. "
        "See https://github.com/pinsonchen/vivify/tree/main/docs/probes.md\n",
        encoding="utf-8",
    )
    (fixers_dir / "README.md").write_text(
        "# User fixers\n\nDrop Python modules with a `FIXER` (or `FIXERS`) export here.\n",
        encoding="utf-8",
    )


def _guess_language(signals) -> str:
    """从扩展名频率猜测主语言。"""
    lang_map = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby",
        ".md": "Markdown", ".html": "HTML", ".css": "CSS",
    }
    if signals.file_extensions:
        top_ext = signals.file_extensions.most_common(1)[0][0]
        return lang_map.get(top_ext, top_ext)
    return ""


def _scenario_from_value(value: str) -> ScenarioType:
    """将字符串值转为 ScenarioType 枚举。"""
    for s in ScenarioType:
        if s.value == value:
            return s
    return ScenarioType.GENERIC


def detect_harness_commands(project_root: Path) -> dict[str, str]:
    """Auto-detect project test/lint/typecheck/build commands by stack.

    Inspection is best-effort: failures simply omit the affected command.
    Returns a possibly-empty dict with any of the keys ``test``, ``lint``,
    ``typecheck``, ``build``.
    """
    commands: dict[str, str] = {}

    def _has_tool(name: str) -> bool:
        return shutil.which(name) is not None

    # Python project
    if (project_root / "pyproject.toml").exists() or (project_root / "setup.py").exists():
        commands["test"] = "python -m pytest --tb=short -q"
        if _has_tool("ruff"):
            commands["lint"] = "ruff check ."
        elif _has_tool("flake8"):
            commands["lint"] = "flake8 ."
        if _has_tool("mypy"):
            commands["typecheck"] = "mypy ."
        return commands

    # Node.js project
    if (project_root / "package.json").exists():
        try:
            pkg = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {}) or {}
            if "test" in scripts:
                commands["test"] = "npm test"
            if "lint" in scripts:
                commands["lint"] = "npm run lint"
            if "build" in scripts:
                commands["build"] = "npm run build"
            if _has_tool("tsc") or (project_root / "tsconfig.json").exists():
                commands["typecheck"] = "npx tsc --noEmit"
        except (json.JSONDecodeError, OSError):
            pass
        return commands

    # Go project
    if (project_root / "go.mod").exists():
        commands["test"] = "go test ./..."
        if _has_tool("golangci-lint"):
            commands["lint"] = "golangci-lint run"
        commands["build"] = "go build ./..."
        return commands

    # Rust project
    if (project_root / "Cargo.toml").exists():
        commands["test"] = "cargo test"
        if _has_tool("clippy-driver") or _has_tool("cargo-clippy"):
            commands["lint"] = "cargo clippy"
        commands["build"] = "cargo build"
        return commands

    return commands


def _build_ai_goals(goals_markdown: str, owner: str) -> str:
    """包装 AI 生成的 GOALS 内容为完整文件。"""
    owner_clean = owner.lstrip("@") or "team"
    header = f"""---
version: 1
owner: "@{owner_clean}"
review_cadence: weekly
---

# Project Goals

This file is read by `vivify goals decompose` to derive concrete
FeatureRequests on a schedule. Each goal must declare at least one KPI.
"""
    # goals_markdown 可能已经包含 header，检查一下
    if goals_markdown.startswith("---"):
        return goals_markdown
    return header + "\n" + goals_markdown


def _build_quick_yaml(
    profile,
    config_values: dict[str, str],
    default_branch: str,
    harness_commands: dict[str, str] | None = None,
    github_token: str = "",
    wiki_path: str = "",
) -> str:
    """生成精简配置（约30行），仅包含必需字段 + 注释引导."""
    name = config_values.get("project.name", "")
    scenario = profile.primary_scenario.value
    language = profile.language
    hc = harness_commands or {}

    lines = [
        "# ============================================",
        "# vivify 快速启动配置 (Quick Start)",
        "# 完整配置: vivify init --template full",
        "# 配置文档: https://github.com/pinsonchen/vivify/docs/config.md",
        "# ============================================",
        "",
        "version: 1",
        "mode: daemon",
        "interval_seconds: 300",
        "",
        "project:",
        f'  name: "{name}"',
        f'  type: "{scenario}"',
        f'  language: "{language}"',
    ]

    # wiki_path
    if wiki_path:
        lines.append(f'  wiki_path: "{wiki_path}"')

    lines.extend([
        "",
        "pr:",
        f"  base_branch: {default_branch}",
        "",
        "agent:",
        "  type: qodercli",
        "  qodercli:",
        "    model: ultimate",
    ])

    # GitHub token (if provided)
    if github_token:
        lines.extend([
            "",
            "github:",
            "  enabled: true",
            f'  token: "{github_token}"',
        ])

    # Harness (only if commands detected)
    if hc:
        lines.extend(["", "harness:", "  enabled: true"])
        if hc.get("test"):
            lines.append(f'  test_command: "{hc["test"]}"')
        if hc.get("lint"):
            lines.append(f'  lint_command: "{hc["lint"]}"')
        if hc.get("typecheck"):
            lines.append(f'  typecheck_command: "{hc["typecheck"]}"')
        if hc.get("build"):
            lines.append(f'  build_command: "{hc["build"]}"')

    lines.append("")
    return "\n".join(lines) + "\n"


def _build_advanced_yaml(
    profile,
    probes: list[str],
    fixers: list[str],
    harness_commands: dict[str, str] | None = None,
) -> str:
    """生成高级配置文件，供 quick 模板用户按需调优."""
    scenario = profile.primary_scenario.value
    _ = harness_commands or {}  # 预留：后续如需可根据实际命令往 advanced 写入调优参数

    lines = [
        "# ============================================",
        "# vivify 高级配置 (Advanced Configuration)",
        "# 本文件可选，用于覆盖默认的高级参数",
        "# 删除本文件不影响基础功能",
        "# ============================================",
        "",
        "# ── AI Agent 调优 (QoderCli Tuning) ──",
        "agent:",
        "  qodercli:",
    ]

    # 使用场景预设
    from vivify.config.presets import get_preset
    preset = get_preset(scenario)
    for key, value in preset.items():
        lines.append(f"    {key}: {value}")

    lines.extend([
        '    extra_args: ["--yolo", "-q"]',
        "    max_concurrent_processes: 10",
        "    permission_mode: bypass_permissions",
        "",
        "# ── 检测探针 (Probes) ──",
        "probes:",
        "  enabled:",
    ])
    for p in probes:
        lines.append(f"    - {p}")

    lines.extend([
        "",
        "# ── 修复器 (Fixers) ──",
        "fixers:",
        "  enabled:",
    ])
    for f in fixers:
        lines.append(f"    - {f}")

    # Harness 高级参数
    lines.extend([
        "",
        "# ── Harness 高级参数 ──",
        "harness:",
        "  run_tests_after_fix: true",
        "  run_lint_after_fix: true",
        "  max_feedback_retries: 2",
        "  feedback_timeout_seconds: 120",
        '  guides_dir: ".vivify/guides"',
        "  inject_guides_to_prompt: true",
        "  doom_loop_window: 10",
        "  doom_loop_threshold: 3",
        "  risk_scoring_enabled: true",
        "  high_risk_requires_tests: true",
    ])

    # Intelligence
    lines.extend([
        "",
        "# ── 智能分析 (Intelligence) ──",
        "intelligence:",
        "  rca_enabled: true",
        "  rca_recurrence_threshold: 3",
        "  trend_enabled: true",
        "  trend_interval_rounds: 10",
    ])

    # Escalation
    lines.extend([
        "",
        "# ── 升级策略 (Escalation) ──",
        "escalation:",
        "  max_same_issue_rounds: 3",
        "  upgrade_threshold: 3",
    ])

    lines.append("")
    return "\n".join(lines) + "\n"


def _build_yaml(
    profile,
    config_values: dict[str, str],
    probes: list[str],
    fixers: list[str],
    default_branch: str,
    github_token: str = "",
    wiki_path: str = "",
    harness_commands: dict[str, str] | None = None,
) -> str:
    """生成 .vivify.yml 内容（纯字符串拼接）。"""
    name = config_values.get("project.name", "")
    description = config_values.get("project.description", "")
    scenario = profile.primary_scenario.value
    language = profile.language
    framework = profile.framework or ""
    deploy_url = config_values.get("deploy.url", "")
    health_endpoint = config_values.get("deploy.health_endpoint", "")
    test_command = config_values.get("commands.test", "")
    build_command = config_values.get("commands.build", "")

    lines = [
        "version: 1",
        "mode: daemon",
        "interval_seconds: 300",
        "state_dir: .vivify",
        "log_dir: .vivify/logs",
        "",
        "# ── 项目基础信息 (Project Info) ──",
        "project:",
        f'  name: "{name}"',
        f'  description: "{description}"',
        f'  type: "{scenario}"',
        f'  language: "{language}"',
        f'  framework: "{framework}"',
        f'  deploy_url: "{deploy_url}"',
        '  deploy_method: "manual"',
        f'  health_endpoint: "{health_endpoint}"',
        f'  test_command: "{test_command}"',
        f'  build_command: "{build_command}"',
        '  dev_command: ""',
        f'  wiki_path: "{wiki_path}"',
        "",
        "# ── PR 创建策略 (Pull Request) ──",
        "pr:",
        f"  base_branch: {default_branch}",
        "  label: vivify",
        "  auto_merge: false",
        "",
        "# ── 检测探针 (Probes - 自动选择) ──",
        "probes:",
        "  enabled:",
    ]
    for p in probes:
        lines.append(f"    - {p}")

    lines.append("")
    lines.append("# ── 修复器 (Fixers - 自动选择) ──")
    lines.append("fixers:")
    lines.append("  enabled:")
    for f in fixers:
        lines.append(f"    - {f}")

    # -- agent 配置 --（无论 qodercli 是否可用都生成，方便后续安装）
    lines.append("")
    lines.append("# ── AI Agent 配置 (通常无需修改) ──")
    lines.append("agent:")
    lines.append("  type: qodercli")
    lines.append("  qodercli:")
    lines.append("    binary_path: qodercli")
    lines.append("    model: ultimate")
    lines.append("    max_turns_fix: 30  # [高级]")
    lines.append("    max_turns_develop: 100  # [高级]")
    lines.append("    max_turns_evaluate: 20  # [高级]")
    lines.append("    max_turns_verify: 20  # [高级]")
    lines.append("    max_turns_decompose: 30  # [高级]")
    lines.append("    timeout_fix_seconds: 1800  # [高级]")
    lines.append("    timeout_develop_seconds: 3600  # [高级]")
    lines.append("    extra_args: [\"--yolo\", \"-q\"]  # [高级]")
    lines.append("    max_concurrent_processes: 10  # [高级]")

    # -- github 配置（实例级）--
    lines.append("")
    lines.append("# ── GitHub 认证 ──")
    lines.append("github:")
    lines.append("  enabled: true")
    lines.append('  token_env: "GH_TOKEN"')
    if github_token:
        lines.append(f'  token: "{github_token}"  # 实例级 token，优先级高于环境变量')
    else:
        lines.append('  token: ""  # 可填入实例级 token，优先级高于环境变量')
    lines.append("  mirror_issues: true")

    # -- harness 配置 --
    hc = harness_commands or {}
    lines.append("")
    lines.append("# ── 验证传感器 (Harness - 自动检测) ──")
    lines.append("harness:")
    lines.append("  enabled: true")
    lines.append(f'  test_command: "{hc.get("test", "")}"')
    lines.append(f'  lint_command: "{hc.get("lint", "")}"')
    lines.append(f'  typecheck_command: "{hc.get("typecheck", "")}"')
    lines.append(f'  build_command: "{hc.get("build", "")}"')
    lines.append("  run_tests_after_fix: true")
    lines.append("  run_lint_after_fix: true")
    lines.append("  max_feedback_retries: 2")
    lines.append("  feedback_timeout_seconds: 120")
    lines.append('  guides_dir: ".vivify/guides"')
    lines.append("  inject_guides_to_prompt: true")
    lines.append("  doom_loop_window: 10")
    lines.append("  doom_loop_threshold: 3")
    lines.append("  risk_scoring_enabled: true")
    lines.append("  high_risk_requires_tests: true")

    lines.append("")
    return "\n".join(lines) + "\n"


def _save_env_token(token: str) -> None:
    """将 GH_TOKEN 保存到 ~/.vivify/env"""
    env_dir = Path.home() / ".vivify"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_file = env_dir / "env"

    # 读取现有内容，避免重复
    existing_lines = []
    if env_file.exists():
        existing_lines = env_file.read_text().splitlines()

    # 替换或添加 GH_TOKEN
    new_lines = [line for line in existing_lines if not line.startswith("GH_TOKEN=")]
    new_lines.append(f"GH_TOKEN={token}")

    env_file.write_text("\n".join(new_lines) + "\n")
    env_file.chmod(0o600)  # 仅 owner 可读写


def _get_gh_token_from_cli() -> str | None:
    """从 gh auth 中直接提取可用 token（零交互）。"""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _load_env_token() -> str:
    """从 ~/.vivify/env 加载已保存的 GH_TOKEN。"""
    env_file = Path.home() / ".vivify" / "env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("GH_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    repo.mkdir(parents=True, exist_ok=True)

    cfg_dest = repo / ".vivify.yml"
    non_interactive: bool = getattr(args, "non_interactive", False)

    # 如果配置已存在且未指定 --force，提示并退出
    if cfg_dest.exists() and not args.force:
        print(f"错误: {cfg_dest} 已存在。使用 --force 覆盖。")
        return 1

    print(f"vivify init → {repo}")

    # ── Step 1: 扫描项目 ──
    print("\n扫描项目结构...")
    signals = Scanner(repo).scan()

    # -- 检测 qodercli --
    analyzer = AIAnalyzer(binary_path=args.qodercli_path)
    qodercli_available, qodercli_info = analyzer.is_available()
    if qodercli_available:
        print(f"  qodercli:    {qodercli_info} (AI 决策已启用)")
    else:
        print("  [ERROR] qodercli 未找到。vivify 的智能引擎依赖 qodercli 运行。")
        print("  请先安装 qodercli: https://docs.qoder.ai/install")
        print("  或指定路径: vivify init --qodercli-path /path/to/qodercli")
        sys.exit(1)

    # ── Step 2.5: 生成项目 Wiki（可选，失败不阻塞）──
    print("\n📖 Step 2.5: 生成项目 Wiki（qodercli wiki）…")
    wiki_context: WikiContext | None = None
    wiki_path_for_yaml = ""
    existing_meta = repo / DEFAULT_WIKI_DIR / "meta" / "repowiki-metadata.json"
    if existing_meta.is_file() and not args.force:
        print(f"  检测到已有 wiki ({DEFAULT_WIKI_DIR})，跳过生成（使用 --force 重新生成）")
        wiki_context = parse_wiki_metadata(repo)
    else:
        print("  正在调用 qodercli wiki（预计 30-60s）…")
        ok, info = generate_wiki(
            repo,
            qodercli_path=args.qodercli_path,
            language="zh",
            timeout_seconds=120,
        )
        if ok:
            print("  Wiki:        生成成功 ✓")
            wiki_context = parse_wiki_metadata(repo)
        else:
            print(f"  Wiki:        生成失败（{info}），跳过。后续可手动运行 `qodercli wiki --repo .`")

    if wiki_context is not None and not wiki_context.is_empty():
        wiki_path_for_yaml = wiki_context.wiki_path or DEFAULT_WIKI_DIR
        print(
            f"  Wiki 上下文: 源文件 {wiki_context.source_file_count} 个，"
            f"代码片段 {wiki_context.snippet_count} 个，"
            f"文档章节 {wiki_context.catalog_count} 个"
        )

    # ── Step 2.6: 构建项目知识图谱（可选，失败不阻塞）──
    print("\n🧠 Step 2.6: 构建项目知识图谱…")
    try:
        kg_builder = KnowledgeBuilder(
            project_root=repo,
            qodercli_binary=args.qodercli_path,
            wiki_path=wiki_path_for_yaml,
            timeout=120,
        )
        kg_graph = kg_builder.build_full()
        kg_node_count = len(kg_graph.nodes) if kg_graph else 0
        kg_edge_count = len(kg_graph.edges) if kg_graph else 0
        print(f"  知识图谱:    {kg_node_count} 节点, {kg_edge_count} 边 ✓")
    except Exception as e:
        print(f"  知识图谱:    构建失败（非阻塞）: {e}")

    # === GitHub 认证配置 ===
    print("\n📌 Step 1.5: 检查 GitHub 认证...")
    gh_token = os.environ.get("GH_TOKEN", "")
    instance_gh_token = ""  # 将写入 .vivify.yml 的实例级 token
    gh_authenticated = False

    if gh_token:
        print("  GH_TOKEN:    已配置 (环境变量) ✓")
        gh_authenticated = True
    else:
        # 先检查 ~/.vivify/env
        saved_token = _load_env_token()
        if saved_token:
            gh_token = saved_token
            print("  GH_TOKEN:    已配置 (~/.vivify/env) ✓")
            gh_authenticated = True
        else:
            # 尝试从 gh auth 直接提取 token（零交互）
            cli_token = _get_gh_token_from_cli()
            if cli_token:
                gh_token = cli_token
                instance_gh_token = cli_token
                _save_env_token(cli_token)  # 缓存到 ~/.vivify/env
                print("  GH_TOKEN:    已从 gh auth 自动提取 ✓")
                gh_authenticated = True
            else:
                print("  GH_TOKEN:    未配置")
                print("  gh CLI:      未认证")

    if not gh_authenticated:
        print()
        print("  ⚠️  GitHub 认证未配置，vivify 将无法自动创建 PR。")
        print("  请提供 GitHub Personal Access Token (需要 repo 权限):")
        print()

        if not non_interactive:
            token = input("  GH_TOKEN (留空跳过): ").strip()
            if token:
                # 实例级：写入 .vivify.yml（主要）
                instance_gh_token = token
                # 全局 fallback：同时保存到 ~/.vivify/env
                _save_env_token(token)
                gh_authenticated = True
                print("  ✅ Token 将写入项目 .vivify.yml 配置（实例级）")
                print("  ✅ 同时保存一份到 ~/.vivify/env作为全局 fallback")
            else:
                print("  ⏭️  跳过。后续可通过 'export GH_TOKEN=...' 或编辑 ~/.vivify/env 配置")
        else:
            print("  提示: 设置 GH_TOKEN 环境变量或运行 'gh auth login'")

    # ── Step 2: 分类项目 ──
    print("分析项目类型...")
    ai_result = None

    # AI 分析
    print("\n  正在使用 AI 分析项目...")
    ai_result = analyzer.analyze(repo, signals, wiki_context=wiki_context)

    if not ai_result:
        # 重试一次
        print("  AI 分析首次未成功，正在重试...")
        ai_result = analyzer.analyze(repo, signals, wiki_context=wiki_context)

    if ai_result:
        # AI 成功 - 构造 profile
        try:
            scenario = ScenarioType(ai_result.scenario)
        except ValueError:
            scenario = ScenarioType.GENERIC
        profile = ProjectProfile(
            primary_scenario=scenario,
            secondary_scenarios=[],
            confidence=ai_result.confidence,
            language=ai_result.language,
            framework=ai_result.framework or "",
            reasoning=ai_result.reasoning,
        )
        print(f"  AI 分析完成: {profile.primary_scenario.value} (置信度: {profile.confidence:.0%})")
    elif args.type:
        # 用户手动指定了 --type
        scenario = ScenarioType(args.type)
        profile = ProjectProfile(
            primary_scenario=scenario,
            secondary_scenarios=[],
            confidence=1.0,
            language=_guess_language(signals),
            framework="",
            reasoning=f"用户手动指定: {args.type}",
        )
        print(f"  使用手动指定类型: {args.type}")
        ai_result = None
    else:
        print("  [ERROR] AI 分析失败。请检查 qodercli 配置或使用 --type 手动指定项目类型。")
        print(f"  可用类型: {', '.join(s.value for s in ScenarioType)}")
        sys.exit(1)

    # 如果 --type 手动指定，覆盖 primary_scenario
    if args.type:
        profile.primary_scenario = _scenario_from_value(args.type)

    # ── Step 3: 展示分类结果并确认 ──
    if not non_interactive:
        print(f"\n  检测到项目类型: {profile.primary_scenario.value}")
        print(f"  主要语言: {profile.language}")
        print(f"  分析理由: {profile.reasoning}")
        print()
        try:
            answer = input("  [Y/n] 确认？(输入其他类型名称可覆盖，如 web-app) ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer.lower() == "n":
            print("  已取消。")
            return 1
        if answer and answer.lower() not in ("y", "yes", ""):
            # 尝试将输入作为场景类型
            valid_values = [s.value for s in ScenarioType]
            if answer in valid_values:
                profile.primary_scenario = _scenario_from_value(answer)
                print(f"  已覆盖为: {profile.primary_scenario.value}")
            else:
                print(f"  未识别的类型 '{answer}'，使用检测结果。")

    # ── Step 4: 确定配置需求 ──
    configurator = Configurator()
    questions = configurator.required_config(profile)

    # ── Step 5: 自动发现填充 ──
    questions = configurator.auto_discover(signals, questions)

    # -- AI 发现的值覆盖/补充自动发现 --
    if ai_result:
        ai_overrides = {
            "project.deploy_url": ai_result.deploy_url,
            "project.test_command": ai_result.test_command,
            "project.build_command": ai_result.build_command,
            "project.dev_command": ai_result.dev_command,
            "project.health_endpoint": ai_result.health_endpoint,
            "project.description": ai_result.description,
        }
        for q in questions:
            override = ai_overrides.get(q.key)
            if override and not q.default:
                q.default = override
                q.source = "AI 分析"

    # ── Step 6: 展示已发现的配置 ──
    discovered = [(q.key, q.default, q.source) for q in questions if q.default and q.source]
    if discovered:
        print("\n  已自动发现:")
        for key, val, src in discovered:
            print(f"    {key} = {val} (来源: {src})")

    # ── Step 7: 交互式问答 ──
    interviewer = Interviewer()
    config_values = interviewer.conduct(questions, non_interactive=non_interactive)

    # ── Step 8: 生成探针和修复器列表 ──
    probes = configurator.get_probes(profile)
    fixers = configurator.get_fixers(profile)

    # 如果有 deploy_url，确保包含 site_health
    deploy_url = config_values.get("deploy.url", "")
    if deploy_url and "site_health" not in probes:
        probes.append("site_health")

    # ── Step 9: 生成 .vivify.yml ──
    default_branch = signals.default_branch or "main"
    # Auto-detect harness commands (best-effort)
    try:
        harness_commands = detect_harness_commands(repo)
        if harness_commands:
            print("\n✨ Harness 环境检测:")
            for k, v in harness_commands.items():
                print(f"    {k:10s} = {v}")
    except Exception as e:
        print(f"  Harness 检测失败 (非阻塞): {e}")
        harness_commands = {}
    if getattr(args, "template", "full") == "quick":
        yaml_content = _build_quick_yaml(
            profile, config_values, default_branch,
            harness_commands=harness_commands,
            github_token=instance_gh_token,
            wiki_path=wiki_path_for_yaml,
        )
    else:
        yaml_content = _build_yaml(
            profile, config_values, probes, fixers, default_branch,
            github_token=instance_gh_token,
            wiki_path=wiki_path_for_yaml,
            harness_commands=harness_commands,
        )
    cfg_dest.parent.mkdir(parents=True, exist_ok=True)
    cfg_dest.write_text(yaml_content, encoding="utf-8")

    # quick 模板：额外生成 .vivify-advanced.yml（可选调优文件）
    if getattr(args, "template", "full") == "quick":
        advanced_dest = repo / ".vivify-advanced.yml"
        if advanced_dest.exists() and not args.force:
            print(f"  {advanced_dest.name} 已存在，跳过（使用 --force 覆盖）")
        else:
            advanced_content = _build_advanced_yaml(
                profile, probes, fixers, harness_commands=harness_commands
            )
            advanced_dest.write_text(advanced_content, encoding="utf-8")
            print(f"  {advanced_dest.name} 生成 ✓ （可选调优，删除不影响基础功能）")

    # ── Step 10: 生成 GOALS.md ──
    goals_dest = repo / "GOALS.md"
    owner = config_values.get("project.name", "team")
    if ai_result and ai_result.goals_markdown:
        goals_content = _build_ai_goals(ai_result.goals_markdown, owner)
    else:
        goals_content = render_goals(profile.primary_scenario.value, owner=owner)
    if goals_dest.exists() and not args.force:
        print("\n  GOALS.md 已存在，跳过 (使用 --force 覆盖)")
    else:
        goals_dest.write_text(goals_content, encoding="utf-8")

    # ── Step 11: 创建目录和文件 ──
    _copy_template("pr_template.md.tmpl", repo / ".vivify" / "pr_template.md", force=args.force)
    _write_user_dir_readmes(repo)

    # ── Step 11.5: 生成默认 harness guides ──
    try:
        from vivify.harness.guides import GuidesManager
        guides_dir = repo / ".vivify" / "guides"
        if not guides_dir.exists() or args.force:
            GuidesManager(guides_dir).generate_default_guides({
                "language": profile.language,
                "test_framework": (harness_commands or {}).get("test", ""),
                "conventions": "",
            })
            print(f"  Harness guides: {guides_dir} ✓")
        else:
            print(f"  Harness guides: {guides_dir} 已存在，跳过")
    except Exception as e:
        print(f"  Harness guides: 生成失败（非阻塞）: {e}")

    # ── Step 12: 更新 .gitignore ──
    _patch_gitignore(repo)

    # ── Step 13: 打印总结 ──
    print("\nvivify init 完成!")
    print(f"  项目类型:    {profile.primary_scenario.value}")
    print(f"  主要语言:    {profile.language}")
    print(f"  部署地址:    {deploy_url or '未配置'}")
    print(f"  启用探针:    {len(probes)} 个")
    print(f"  启用修复器:  {len(fixers)} 个")
    print(f"    决策引擎:  AI 驱动 (qodercli {qodercli_info})")
    print()
    print("后续步骤:")
    print("  vivify doctor")
    print("  vivify run --once --dry-run")
    print("  vivify start")
    return 0


__all__ = ["register", "run"]
