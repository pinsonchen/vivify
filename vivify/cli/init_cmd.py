"""``vivify init`` — scaffold a new repo for vivify use (with intelligent analysis)."""
from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path

from vivify.config.defaults import DEFAULT_GITIGNORE_ENTRIES
from vivify.intelligence import (
    Classifier,
    Configurator,
    Interviewer,
    Scanner,
    ScenarioType,
)
from vivify.intelligence.goals_templates import render_goals


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


def _scenario_from_value(value: str) -> ScenarioType:
    """将字符串值转为 ScenarioType 枚举。"""
    for s in ScenarioType:
        if s.value == value:
            return s
    return ScenarioType.GENERIC


def _build_yaml(
    profile,
    config_values: dict[str, str],
    probes: list[str],
    fixers: list[str],
    default_branch: str,
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
        "",
        "pr:",
        f"  base_branch: {default_branch}",
        "  label: vivify",
        "  auto_merge: false",
        "",
        "probes:",
        "  enabled:",
    ]
    for p in probes:
        lines.append(f"    - {p}")

    lines.append("")
    lines.append("fixers:")
    lines.append("  enabled:")
    for f in fixers:
        lines.append(f"    - {f}")

    lines.append("")
    return "\n".join(lines) + "\n"


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

    # ── Step 2: 分类项目 ──
    print("分析项目类型...")
    profile = Classifier().classify(signals)

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
    yaml_content = _build_yaml(profile, config_values, probes, fixers, default_branch)
    cfg_dest.parent.mkdir(parents=True, exist_ok=True)
    cfg_dest.write_text(yaml_content, encoding="utf-8")

    # ── Step 10: 生成 GOALS.md ──
    goals_dest = repo / "GOALS.md"
    owner = config_values.get("project.name", "team")
    goals_content = render_goals(profile.primary_scenario.value, owner=owner)
    if goals_dest.exists() and not args.force:
        print("\n  GOALS.md 已存在，跳过 (使用 --force 覆盖)")
    else:
        goals_dest.write_text(goals_content, encoding="utf-8")

    # ── Step 11: 创建目录和文件 ──
    _copy_template("pr_template.md.tmpl", repo / ".vivify" / "pr_template.md", force=args.force)
    _write_user_dir_readmes(repo)

    # ── Step 12: 更新 .gitignore ──
    _patch_gitignore(repo)

    # ── Step 13: 打印总结 ──
    print("\nvivify init 完成!")
    print(f"  项目类型:    {profile.primary_scenario.value}")
    print(f"  主要语言:    {profile.language}")
    print(f"  部署地址:    {deploy_url or '未配置'}")
    print(f"  启用探针:    {len(probes)} 个")
    print(f"  启用修复器:  {len(fixers)} 个")
    print()
    print("后续步骤:")
    print("  vivify doctor")
    print("  vivify run --once --dry-run")
    print("  vivify start")
    return 0


__all__ = ["register", "run"]
