"""``auto-heal probes`` — list / test / enable / disable."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from auto_heal.config.loader import load_config
from auto_heal.interfaces.probe import ProbeContext
from auto_heal.probes.registry import build_default_registry
from auto_heal.storage.sqlite_provider import SqliteStorageProvider


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("probes", help="Probe management.")
    sp = p.add_subparsers(dest="probes_cmd", required=True)

    pl = sp.add_parser("list", help="List discovered probes.")
    pl.add_argument("--enabled-only", action="store_true")
    pl.set_defaults(func=run_list)

    pt = sp.add_parser("test", help="Run a single probe and print its issues.")
    pt.add_argument("probe_id")
    pt.add_argument("--json", action="store_true")
    pt.set_defaults(func=run_test)

    en = sp.add_parser("enable", help="Add a probe to the enabled list (.auto-heal.yml).")
    en.add_argument("probe_id")
    en.set_defaults(func=run_enable)

    di = sp.add_parser("disable", help="Remove a probe from the enabled list.")
    di.add_argument("probe_id")
    di.set_defaults(func=run_disable)


def _registry(args):
    cfg = load_config(getattr(args, "config", None))
    return cfg, build_default_registry(user_dir=Path(cfg.probes.user_probes_dir))


def run_list(args: argparse.Namespace) -> int:
    cfg, reg = _registry(args)
    enabled = set(cfg.probes.enabled)
    for p in sorted(reg.all(), key=lambda p: p.id):
        is_enabled = p.id in enabled
        if args.enabled_only and not is_enabled:
            continue
        flag = "*" if is_enabled else " "
        print(f"{flag} {p.id:<32} {p.description or ''}")
    return 0


def run_test(args: argparse.Namespace) -> int:
    cfg, reg = _registry(args)
    probe = reg.get(args.probe_id)
    if probe is None:
        print(f"probe not found: {args.probe_id}")
        return 1
    storage = SqliteStorageProvider(cfg.storage.sqlite.path)
    storage.initialize()
    ctx = ProbeContext(
        repo_root=Path.cwd().resolve(),
        config=cfg, storage=storage, logger=logging.getLogger("auto_heal.cli.probes"),
    )
    raw = probe.collect(ctx)
    issues = probe.analyze(raw or {}, ctx)
    if args.json:
        print(json.dumps([i.to_dict() for i in issues], indent=2, ensure_ascii=False))
    else:
        if not issues:
            print(f"{probe.id}: no issues")
            return 0
        for i in issues:
            print(f"[{i.level.value}] {i.category}: {i.title}")
    return 0


def _rewrite_enabled(args, *, transform) -> int:
    """Mutate cfg.probes.enabled and write back to .auto-heal.yml."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        print("PyYAML required to edit .auto-heal.yml")
        return 2
    path = Path(getattr(args, "config", None) or ".auto-heal.yml")
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    probes = raw.setdefault("probes", {})
    enabled = list(probes.get("enabled", []))
    enabled = transform(enabled)
    probes["enabled"] = enabled
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    print(f"updated {path} (probes.enabled = {enabled})")
    return 0


def run_enable(args: argparse.Namespace) -> int:
    return _rewrite_enabled(
        args,
        transform=lambda lst: lst + ([args.probe_id] if args.probe_id not in lst else []),
    )


def run_disable(args: argparse.Namespace) -> int:
    return _rewrite_enabled(
        args, transform=lambda lst: [p for p in lst if p != args.probe_id],
    )


__all__ = ["register"]
