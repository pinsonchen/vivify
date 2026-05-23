"""``vivify start|stop|status|list`` — daemon lifecycle management."""
from __future__ import annotations

import argparse
from pathlib import Path

from vivify.config.loader import load_config
from vivify.daemon.manager import DaemonManager


def register(sub: argparse._SubParsersAction) -> None:
    """注册 start/stop/status/list 子命令。"""
    # start
    p_start = sub.add_parser("start", help="Start vivify daemon in background.")
    p_start.add_argument("--interval", type=int, default=None,
                         help="Override interval_seconds from config.")
    p_start.add_argument("--category", default=None,
                         help="Only process issues of this category.")
    p_start.add_argument("--dry-run", action="store_true",
                         help="Detect issues but skip fixes/PRs.")
    p_start.set_defaults(func=_cmd_start)

    # stop
    p_stop = sub.add_parser("stop", help="Stop vivify daemon for current directory.")
    p_stop.add_argument("--force", action="store_true",
                        help="Force kill immediately (SIGKILL).")
    p_stop.set_defaults(func=_cmd_stop)

    # status
    p_status = sub.add_parser("status", help="Show daemon status for current directory.")
    p_status.set_defaults(func=_cmd_status)

    # list
    p_list = sub.add_parser("list", help="List all running vivify instances on this machine.")
    p_list.set_defaults(func=_cmd_list)


def _get_manager(args: argparse.Namespace) -> DaemonManager:
    cfg = load_config(getattr(args, "config", None))
    repo_root = Path.cwd().resolve()
    return DaemonManager(repo_root=repo_root, state_dir=Path(cfg.state_dir))


def _cmd_start(args: argparse.Namespace) -> int:
    mgr = _get_manager(args)
    if mgr.is_running():
        status = mgr.status()
        print(f"vivify is already running (PID {status.pid})")
        print(f"  repo: {status.repo_root}")
        print(f"  started: {status.started_at}")
        return 1

    # 构建传递给 vivify run 的额外参数
    extra_args = []
    if args.interval is not None:
        extra_args.extend(["--interval", str(args.interval)])
    if args.category:
        extra_args.extend(["--category", args.category])
    if args.dry_run:
        extra_args.append("--dry-run")

    pid = mgr.start(extra_args=extra_args or None)
    print(f"vivify daemon started (PID {pid})")
    print(f"  repo: {mgr.repo_root}")
    print(f"  state: {mgr.state_dir}")
    print()
    print("Use 'vivify status' to check or 'vivify stop' to stop.")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    mgr = _get_manager(args)
    if not mgr.is_running():
        print("No vivify daemon running for this directory.")
        return 0

    status = mgr.status()
    print(f"Stopping vivify daemon (PID {status.pid})...")

    grace = 30
    if hasattr(args, "config"):
        try:
            cfg = load_config(args.config)
            grace = cfg.daemon.stop_grace_seconds if hasattr(cfg, "daemon") else 30
        except Exception:
            pass

    success = mgr.stop(force=args.force, grace_seconds=grace)
    if success:
        print("Daemon stopped.")
        return 0
    else:
        print("Failed to stop daemon. Try --force.")
        return 1


def _cmd_status(args: argparse.Namespace) -> int:
    mgr = _get_manager(args)
    status = mgr.status()

    if not status.running:
        print("vivify is not running in this directory.")
        return 0

    print("vivify is running")
    print(f"  PID:     {status.pid}")
    print(f"  repo:    {status.repo_root}")
    print(f"  started: {status.started_at or 'unknown'}")
    if status.uptime_seconds is not None:
        hours = int(status.uptime_seconds // 3600)
        minutes = int((status.uptime_seconds % 3600) // 60)
        print(f"  uptime:  {hours}h {minutes}m")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    instances = DaemonManager.list_instances()
    if not instances:
        print("No vivify instances running on this machine.")
        return 0

    print(f"{'PID':<8} {'UPTIME':<12} {'REPO'}")
    print(f"{'---':<8} {'------':<12} {'----'}")
    for inst in instances:
        uptime_str = "?"
        if inst.uptime_seconds is not None:
            h = int(inst.uptime_seconds // 3600)
            m = int((inst.uptime_seconds % 3600) // 60)
            uptime_str = f"{h}h {m}m"
        print(f"{inst.pid:<8} {uptime_str:<12} {inst.repo_root}")
    return 0


__all__ = ["register"]
