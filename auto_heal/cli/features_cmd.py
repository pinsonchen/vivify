"""``auto-heal features`` — list / show / retry."""
from __future__ import annotations

import argparse

from auto_heal.config.loader import load_config
from auto_heal.storage.sqlite_provider import SqliteStorageProvider


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("features", help="Inspect feature requests in the local DB.")
    sp = p.add_subparsers(dest="features_cmd", required=True)

    pl = sp.add_parser("list", help="List feature requests.")
    pl.add_argument("--status")
    pl.add_argument("--limit", type=int, default=50)
    pl.set_defaults(func=run_list)

    sh = sp.add_parser("show", help="Show one feature request in detail.")
    sh.add_argument("id", type=int)
    sh.set_defaults(func=run_show)

    rt = sp.add_parser("retry", help="Reset a feature back to 'approved' for retry.")
    rt.add_argument("id", type=int)
    rt.set_defaults(func=run_retry)


def _storage(args) -> SqliteStorageProvider:
    cfg = load_config(getattr(args, "config", None))
    storage = SqliteStorageProvider(cfg.storage.sqlite.path)
    storage.initialize()
    return storage


def run_list(args: argparse.Namespace) -> int:
    storage = _storage(args)
    rows = storage.list_features(status=args.status, limit=args.limit)
    if not rows:
        print("(no feature requests)")
        return 0
    print(f"{'#id':>4}  {'status':<14}  {'type':<12}  {'pri':<3}  title")
    for fr in rows:
        print(f"{fr.id:>4}  {fr.status:<14}  {fr.type:<12}  "
              f"{(fr.priority or '?'):<3}  {fr.title[:80]}")
    return 0


def run_show(args: argparse.Namespace) -> int:
    storage = _storage(args)
    fr = storage.get_feature(args.id)
    if fr is None:
        print(f"feature #{args.id} not found")
        return 1
    print(f"#{fr.id} [{fr.status}] {fr.title}")
    print(f"  type={fr.type} priority={fr.priority} parent_goal={fr.parent_goal}"
          f" parent_id={fr.parent_id}")
    if fr.pr_url:
        print(f"  PR: {fr.pr_url}")
    if fr.commit_hash:
        print(f"  commit: {fr.commit_hash}")
    if fr.feasibility:
        print(f"\n## Feasibility\n{fr.feasibility}")
    if fr.summary:
        print(f"\n## Summary\n{fr.summary}")
    if fr.development_result:
        print(f"\n## Development result (tail)\n{fr.development_result[-1500:]}")
    print(f"\nDescription:\n{fr.description}")
    return 0


def run_retry(args: argparse.Namespace) -> int:
    storage = _storage(args)
    fr = storage.get_feature(args.id)
    if fr is None:
        print(f"feature #{args.id} not found")
        return 1
    storage.update_feature(args.id, status="approved",
                           development_result="", commit_hash=None, pr_url=None)
    print(f"feature #{args.id} reset to 'approved'; will retry on next run")
    return 0


__all__ = ["register"]
