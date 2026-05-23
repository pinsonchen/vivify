"""``auto-heal logs`` — tail the rotating log file."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from auto_heal.config.loader import load_config


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("logs", help="View kernel logs.")
    sp = p.add_subparsers(dest="logs_cmd", required=True)

    tail = sp.add_parser("tail", help="Print the last N lines (and optionally follow).")
    tail.add_argument("-n", "--lines", type=int, default=200)
    tail.add_argument("-f", "--follow", action="store_true")
    tail.set_defaults(func=run_tail)


def run_tail(args: argparse.Namespace) -> int:
    cfg = load_config(getattr(args, "config", None))
    log_file = Path(cfg.log_dir) / "auto-heal.log"
    if not log_file.exists():
        print(f"no log file at {log_file}")
        return 1
    with log_file.open("r", encoding="utf-8", errors="replace") as fh:
        try:
            data = fh.readlines()
        except Exception:
            data = []
        for line in data[-args.lines:]:
            print(line, end="")
        if args.follow:
            fh.seek(0, 2)
            try:
                while True:
                    chunk = fh.readline()
                    if not chunk:
                        time.sleep(0.5)
                        continue
                    print(chunk, end="")
            except KeyboardInterrupt:  # pragma: no cover
                pass
    return 0


__all__ = ["register"]
