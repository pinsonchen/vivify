"""``auto-heal fixers`` — list / test."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from auto_heal.config.loader import load_config
from auto_heal.fixers.registry import FixerRegistry
from auto_heal.interfaces.fixer import FixContext
from auto_heal.models.issue import Issue
from auto_heal.storage.sqlite_provider import SqliteStorageProvider


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("fixers", help="Direct-fixer management.")
    sp = p.add_subparsers(dest="fixers_cmd", required=True)

    pl = sp.add_parser("list", help="List discovered fixers.")
    pl.set_defaults(func=run_list)

    pt = sp.add_parser("test", help="Run a fixer against a JSON-file Issue.")
    pt.add_argument("fixer_id")
    pt.add_argument("--issue-file", required=True,
                    help="Path to a JSON file matching Issue.to_dict() shape.")
    pt.add_argument("--apply", action="store_true",
                    help="Actually run .fix(); without this, only .can_fix() is invoked.")
    pt.set_defaults(func=run_test)


def _registry(args) -> FixerRegistry:
    cfg = load_config(getattr(args, "config", None))
    reg = FixerRegistry()
    reg.load_builtins()
    reg.load_user_dir(Path(cfg.fixers.user_fixers_dir))
    return reg


def run_list(args: argparse.Namespace) -> int:
    cfg = load_config(getattr(args, "config", None))
    reg = _registry(args)
    enabled = set(cfg.fixers.enabled)
    for f in sorted(reg.all(), key=lambda f: f.id):
        flag = "*" if f.id in enabled else " "
        cats = ",".join(f.handles_categories) or "-"
        print(f"{flag} {f.id:<24} categories={cats}  {f.description or ''}")
    return 0


def run_test(args: argparse.Namespace) -> int:
    cfg = load_config(getattr(args, "config", None))
    reg = _registry(args)
    fixer = reg.get(args.fixer_id)
    if fixer is None:
        print(f"fixer not found: {args.fixer_id}")
        return 1
    raw = json.loads(Path(args.issue_file).read_text(encoding="utf-8"))
    issue = Issue.factory(
        category=raw["category"],
        level=raw.get("level", "MEDIUM"),
        title=raw["title"],
        description=raw.get("description", ""),
        data=raw.get("data", {}),
        source_probe=raw.get("source_probe", "manual"),
    )
    storage = SqliteStorageProvider(cfg.storage.sqlite.path)
    storage.initialize()
    ctx = FixContext(
        repo_root=Path.cwd().resolve(),
        config=cfg, storage=storage,
        logger=logging.getLogger("auto_heal.cli.fixers"),
    )
    can = fixer.can_fix(issue, ctx)
    print(f"{fixer.id}.can_fix = {can}")
    if can and args.apply:
        result = fixer.fix(issue, ctx)
        print(f"  fixed={result.fixed} message={result.message[:200]}")
        if result.changed_files:
            print("  changed_files:")
            for cf in result.changed_files[:20]:
                print(f"    - {cf}")
    return 0


__all__ = ["register"]
