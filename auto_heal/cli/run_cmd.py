"""``auto-heal run`` — drive the kernel loop (foreground daemon)."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

from auto_heal.agents.qodercli_agent import QoderCliAgent, QoderCliConfig as AgentBinConfig
from auto_heal.config.loader import load_config
from auto_heal.fixers.registry import FixerRegistry
from auto_heal.kernel.dispatch import DispatchPolicy
from auto_heal.kernel.escalator import EscalationPolicy
from auto_heal.kernel.health_monitor import HealthMonitor, HealthMonitorConfig
from auto_heal.kernel.loop import Kernel, KernelConfig, KernelDeps
from auto_heal.pr_mode.auto_merge import AutoMerge, AutoMergeConfig
from auto_heal.pr_mode.pr_creator import PrCreator, PrCreatorConfig
from auto_heal.pr_mode.worktree import WorktreeManager
from auto_heal.probes.registry import build_default_registry
from auto_heal.reporter.logger import setup_logging
from auto_heal.storage.sqlite_provider import SqliteStorageProvider


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("run", help="Run the kernel loop.")
    p.add_argument("--once", action="store_true", help="Run a single round and exit.")
    p.add_argument("--dry-run", action="store_true",
                   help="Detect issues but skip fixers / agent / PRs.")
    p.add_argument("--category", help="Limit to a single issue category.")
    p.add_argument("--interval", type=int, default=None,
                   help="Override interval_seconds from config.")
    p.set_defaults(func=run)


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
