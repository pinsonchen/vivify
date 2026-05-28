"""``vivify`` CLI entry point — dispatches to subcommand modules."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from vivify.cli import (
    config_cmd,
    daemon_cmd,
    dashboard_cmd,
    doctor_cmd,
    features_cmd,
    fixers_cmd,
    goals_cmd,
    init_cmd,
    logs_cmd,
    probes_cmd,
    run_cmd,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vivify",
        description="Self-growing intelligent extension for any GitHub repo.",
    )
    parser.add_argument("--config", help="Path to .vivify.yml", default=None)
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="Increase log verbosity (-v INFO, -vv DEBUG)")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd.register(sub)
    run_cmd.register(sub)
    doctor_cmd.register(sub)
    goals_cmd.register(sub)
    probes_cmd.register(sub)
    fixers_cmd.register(sub)
    features_cmd.register(sub)
    logs_cmd.register(sub)
    daemon_cmd.register(sub)
    dashboard_cmd.register(sub)
    config_cmd.register(sub)

    return parser


def cli(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.func(args) or 0


def main() -> None:  # pragma: no cover — exec entry
    sys.exit(cli())


if __name__ == "__main__":  # pragma: no cover
    main()
