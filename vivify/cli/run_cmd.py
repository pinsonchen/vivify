"""``vivify run`` — drive the kernel loop (foreground daemon)."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

from vivify.agents.qodercli_agent import QoderCliAgent, QoderCliConfig as AgentBinConfig
from vivify.config.loader import load_config
from vivify.fixers.registry import FixerRegistry
from vivify.kernel.dispatch import DispatchPolicy
from vivify.kernel.escalator import EscalationPolicy
from vivify.kernel.health_monitor import HealthMonitor, HealthMonitorConfig
from vivify.kernel.loop import Kernel, KernelConfig, KernelDeps
from vivify.pr_mode.auto_merge import AutoMerge, AutoMergeConfig
from vivify.pr_mode.pr_creator import PrCreator, PrCreatorConfig
from vivify.pr_mode.worktree import WorktreeManager
from vivify.probes.registry import build_default_registry
from vivify.reporter.logger import setup_logging
from vivify.storage.sqlite_provider import SqliteStorageProvider


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("run", help="Run the kernel loop.")
    p.add_argument("--once", action="store_true", help="Run a single round and exit.")
    p.add_argument("--dry-run", action="store_true",
                   help="Detect issues but skip fixers / agent / PRs.")
    p.add_argument("--category", help="Limit to a single issue category.")
    p.add_argument("--interval", type=int, default=None,
                   help="Override interval_seconds from config.")
    p.set_defaults(func=run)


def _load_instance_env() -> None:
    """加载实例级和全局环境变量配置（与 daemon/manager.py 保持一致）。

    前台 ``vivify run`` / ``vivify run --once`` 不会经过 DaemonManager.start()，
    需在进入 kernel 之前手动加载同样的环境变量，否则 GH_TOKEN 等不可用。
    """
    # 1. 全局 fallback：~/.vivify/env
    env_file = Path.home() / ".vivify" / "env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
        except OSError:
            pass

    # 2. 实例配置优先：.vivify.yml 中的 github.token
    config_path = Path(".vivify.yml")
    if config_path.exists():
        try:
            import yaml  # 延迟导入，避免启动顺序的硬依赖
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            gh_cfg = cfg.get("github", {}) or {}
            instance_token = gh_cfg.get("token", "") or ""
            if instance_token:
                os.environ["GH_TOKEN"] = instance_token
            token_env_name = gh_cfg.get("token_env", "GH_TOKEN") or "GH_TOKEN"
            if (
                token_env_name != "GH_TOKEN"
                and os.environ.get(token_env_name)
                and not os.environ.get("GH_TOKEN")
            ):
                os.environ["GH_TOKEN"] = os.environ[token_env_name]
        except Exception:
            pass  # 配置读取失败不影响启动


def _build_agent(cfg) -> QoderCliAgent:
    qc = cfg.agent.qodercli
    bin_cfg = AgentBinConfig(
        binary_path=qc.binary_path,
        model=qc.model,
        max_turns_default=qc.max_turns_fix,
        timeout_seconds_default=qc.timeout_fix_seconds,
        extra_args=tuple(qc.extra_args),
        max_concurrent_processes=qc.max_concurrent_processes,
        slot_wait_timeout_seconds=qc.slot_wait_timeout_seconds,
        auto_trust_workspace=qc.auto_trust_workspace,
    )
    return QoderCliAgent(bin_cfg)


def run(args: argparse.Namespace) -> int:
    _load_instance_env()
    cfg = load_config(getattr(args, "config", None))
    log_level = logging.INFO if args.verbose < 2 else logging.DEBUG
    if args.verbose == 0:
        log_level = logging.WARNING
    setup_logging(log_dir=cfg.log_dir, level=log_level)

    repo_root = Path.cwd().resolve()
    state_dir = Path(cfg.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    storage = SqliteStorageProvider(cfg.storage.sqlite.path)
    storage.initialize()

    probe_registry = build_default_registry(user_dir=Path(cfg.probes.user_probes_dir))
    probes = [probe_registry.get(pid) for pid in cfg.probes.enabled
              if probe_registry.get(pid)]

    fixer_registry = FixerRegistry()
    fixer_registry.load_builtins()
    fixer_registry.load_user_dir(Path(cfg.fixers.user_fixers_dir))

    worktrees = WorktreeManager(
        repo_root,
        branch_prefix=cfg.pr.branch_prefix,
        base_branch=cfg.pr.base_branch,
    )
    pr_creator = PrCreator(PrCreatorConfig(
        base_branch=cfg.pr.base_branch,
        default_labels=tuple(cfg.pr.labels),
        default_draft=cfg.pr.draft_default,
    ))
    auto_merge = AutoMerge(AutoMergeConfig(
        enabled=cfg.pr.auto_merge,
        poll_timeout_seconds=cfg.pr.merge_poll_timeout_seconds,
        poll_interval_seconds=10,
    ))

    health_monitor: Optional[HealthMonitor] = None
    if cfg.kpi_monitor.enabled:
        health_monitor = HealthMonitor(
            storage=storage,
            config=HealthMonitorConfig(
                enabled=True,
                check_interval_hours=cfg.kpi_monitor.check_interval_hours,
                degrade_ratio=cfg.kpi_monitor.degrade_ratio,
                baseline_window_days=cfg.kpi_monitor.baseline_window_days,
            ),
        )

    interval = args.interval if args.interval is not None else cfg.interval_seconds
    kernel_cfg = KernelConfig(
        interval_seconds=interval,
        dry_run=args.dry_run,
        enabled_probe_ids=set(cfg.probes.enabled),
        per_probe_timeout_seconds=cfg.probes.per_probe_timeout_seconds,
        only_category=args.category,
        package_root=Path(__file__).resolve().parents[1],
        enable_self_improve_prompt=cfg.self_growth.enabled,
        deploy=cfg.deploy,
        deploy_url=cfg.project.deploy_url,
        goals=cfg.goals,
        default_branch=cfg.pr.base_branch,
    )

    deps = KernelDeps(
        repo_root=repo_root,
        storage=storage,
        agent=_build_agent(cfg),
        probes=probes,
        fixers=fixer_registry,
        worktrees=worktrees,
        pr_creator=pr_creator,
        auto_merge=auto_merge,
        health_monitor=health_monitor,
    )

    dispatch_policy = DispatchPolicy(
        low_cooldown_seconds=cfg.escalation.low_cooldown_seconds,
        medium_cooldown_seconds=cfg.escalation.medium_cooldown_seconds,
        max_same_issue_rounds=cfg.escalation.max_same_issue_rounds,
    )
    escalation_policy = EscalationPolicy(
        upgrade_threshold=cfg.escalation.upgrade_threshold,
    )
    kernel = Kernel(
        deps=deps, config=kernel_cfg,
        dispatch_policy=dispatch_policy, escalation_policy=escalation_policy,
    )

    if args.once:
        report = kernel.run_once()
        print(f"round {report.round_num} done — issues={report.issues_seen}, "
              f"direct_fixes={report.direct_fixes}, agent_fixes={report.agent_fixes}, "
              f"escalations={report.escalations}, "
              f"features={report.features_processed}, "
              f"duration={report.duration_seconds:.1f}s")
        return 0
    kernel.run_forever()
    return 0


__all__ = ["register", "run"]
