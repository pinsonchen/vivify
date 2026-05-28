"""``vivify config`` — configuration inspection and validation tools."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vivify.config.loader import load_config
from vivify.config.schema import VivifyConfig


# Configuration field explanations
FIELD_EXPLANATIONS: dict[str, str] = {
    "version": "配置文件版本号，当前固定为 1",
    "mode": "运行模式: daemon(后台守护) / once(单次运行) / dry-run(模拟运行不提交)",
    "interval_seconds": "daemon 模式下每轮循环的间隔秒数",
    "project.name": "项目名称，用于 PR 标题和日志标识",
    "project.type": (
        "项目场景类型，决定探针和修复器的选择。可选: web-app, api-service, "
        "python-package, cli-tool, docs-only, static-site, mobile-app, monorepo, "
        "infra, generic"
    ),
    "project.language": "项目主要编程语言",
    "pr.base_branch": "PR 的目标分支（通常是 main 或 master）",
    "pr.auto_merge": "是否自动合并通过验证的 PR",
    "agent.qodercli.model": "qodercli 使用的 AI 模型。可选: ultimate, pro",
    "agent.qodercli.max_turns_fix": "修复 issue 时 AI 的最大对话轮数",
    "agent.qodercli.max_turns_develop": "开发 feature 时 AI 的最大对话轮数",
    "agent.qodercli.timeout_fix_seconds": "修复 issue 的超时时间（秒）",
    "agent.qodercli.permission_mode": (
        "AI 执行权限模式: default/accept_edits/bypass_permissions/dont_ask/plan/auto"
    ),
    "harness.enabled": "是否启用 Harness 验证体系（PEV 循环）",
    "harness.test_command": "项目测试命令（如 pytest, npm test），用于 AI 修改后的验证",
    "harness.lint_command": "项目 lint 命令（如 ruff check .），用于代码质量检查",
    "harness.doom_loop_window": "Doom-loop 检测的滑动窗口大小：检查最近 N 次操作是否重复",
    "harness.doom_loop_threshold": "同一操作在窗口内重复 N 次触发 doom-loop 保护",
    "harness.risk_scoring_enabled": "是否启用风险评分：高风险修改需通过更严格验证",
    "intelligence.rca_enabled": "是否启用 AI 根因分析（重复 issue 自动分析根本原因）",
    "intelligence.trend_enabled": "是否启用趋势分析（定期分析 KPI 变化趋势）",
    "escalation.max_same_issue_rounds": "同一 issue 连续修复失败几轮后自动升级",
    "goals.path": "GOALS.md 文件路径，定义项目目标供 AI 分解为 Feature",
    "deploy.method": (
        "部署方式: manual/ssh/rsync/command/webhook/github-pages/vercel/netlify"
    ),
}


def register(sub: argparse._SubParsersAction) -> None:
    """Register the 'config' command group."""
    p = sub.add_parser("config", help="Configuration inspection and management tools.")
    config_sub = p.add_subparsers(dest="config_action")

    # config show
    show_p = config_sub.add_parser("show", help="显示当前生效的完整配置")
    show_p.add_argument(
        "--format", choices=["yaml", "json"], default="yaml", help="输出格式"
    )

    # config validate
    config_sub.add_parser("validate", help="验证配置文件格式和字段有效性")

    # config explain
    explain_p = config_sub.add_parser("explain", help="解释特定配置键的用途")
    explain_p.add_argument(
        "key", nargs="?", help="配置键名 (如 harness.doom_loop_window)"
    )

    # config diff
    config_sub.add_parser("diff", help="显示与默认值的差异")

    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    action = getattr(args, "config_action", None)

    if action == "show":
        return _cmd_show(args)
    elif action == "validate":
        return _cmd_validate(args)
    elif action == "explain":
        return _cmd_explain(args)
    elif action == "diff":
        return _cmd_diff(args)
    else:
        print("用法: vivify config {show|validate|explain|diff}")
        print("  show      显示当前生效的完整配置")
        print("  validate  验证配置文件格式和有效性")
        print("  explain   解释配置键的用途")
        print("  diff      显示与默认值的差异")
        return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """显示当前生效的完整配置（含默认值合并结果）."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"错误: 无法加载配置: {e}", file=sys.stderr)
        return 1

    data = cfg.model_dump()
    fmt = getattr(args, "format", "yaml")

    if fmt == "json":
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        try:
            import yaml
            print(
                yaml.dump(
                    data,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            )
        except ImportError:
            # Fallback to JSON if PyYAML not available
            print(json.dumps(data, indent=2, ensure_ascii=False))

    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """验证配置文件格式和字段有效性."""
    cfg_path = Path(".vivify.yml")

    if not cfg_path.exists():
        print("错误: .vivify.yml 不存在。请先运行 vivify init。", file=sys.stderr)
        return 1

    try:
        cfg = load_config()
        print("✓ 配置文件格式正确")

        # 基础验证
        warnings = []
        if not cfg.project.name:
            warnings.append("project.name 为空")
        if not cfg.project.type or cfg.project.type == "generic":
            warnings.append("project.type 未指定或为 generic")
        if (
            cfg.harness.enabled
            and not cfg.harness.test_command
            and not cfg.harness.lint_command
        ):
            warnings.append("harness 已启用但未配置 test/lint 命令")
        if not cfg.github.token and not cfg.github.token_env:
            warnings.append("GitHub token 未配置")

        if warnings:
            print(f"\n⚠ 发现 {len(warnings)} 个警告:")
            for w in warnings:
                print(f"  - {w}")
        else:
            print("✓ 所有关键字段已配置")

        return 0
    except Exception as e:
        print(f"✗ 配置验证失败: {e}", file=sys.stderr)
        return 1


def _cmd_explain(args: argparse.Namespace) -> int:
    """解释特定配置键的用途."""
    key = getattr(args, "key", None)

    if not key:
        # 列出所有可解释的键
        print("可解释的配置键:\n")
        for k, v in sorted(FIELD_EXPLANATIONS.items()):
            print(f"  {k:40s} {v[:60]}")
        print(f"\n共 {len(FIELD_EXPLANATIONS)} 个配置键。")
        print("用法: vivify config explain <key>")
        return 0

    explanation = FIELD_EXPLANATIONS.get(key)
    if explanation:
        print(f"\n{key}:")
        print(f"  {explanation}")
    else:
        # 尝试模糊匹配
        matches = [k for k in FIELD_EXPLANATIONS if key in k]
        if matches:
            print(f"未找到精确匹配 '{key}'，相关键:")
            for m in matches:
                print(f"  {m}: {FIELD_EXPLANATIONS[m]}")
        else:
            print(f"未找到配置键 '{key}' 的说明。")
            print("使用 'vivify config explain' 查看所有可用键。")
            return 1

    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """显示用户配置与默认值的差异."""
    cfg_path = Path(".vivify.yml")

    if not cfg_path.exists():
        print("错误: .vivify.yml 不存在。", file=sys.stderr)
        return 1

    try:
        user_cfg = load_config()
        default_cfg = VivifyConfig()

        user_data = user_cfg.model_dump()
        default_data = default_cfg.model_dump()

        diffs = _find_diffs(user_data, default_data, prefix="")

        if not diffs:
            print("配置与默认值完全一致（无自定义）。")
        else:
            print(f"发现 {len(diffs)} 个自定义配置:\n")
            for path, user_val, default_val in diffs:
                print(f"  {path}:")
                print(f"    当前值:  {user_val}")
                print(f"    默认值:  {default_val}")
                print()

        return 0
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


def _find_diffs(user: dict, default: dict, prefix: str) -> list[tuple[str, str, str]]:
    """递归比较两个 dict，找出差异."""
    diffs = []
    for key in set(list(user.keys()) + list(default.keys())):
        path = f"{prefix}.{key}" if prefix else key
        u_val = user.get(key)
        d_val = default.get(key)

        if isinstance(u_val, dict) and isinstance(d_val, dict):
            diffs.extend(_find_diffs(u_val, d_val, path))
        elif u_val != d_val:
            diffs.append((path, str(u_val), str(d_val)))

    return diffs


__all__ = ["register", "run"]
