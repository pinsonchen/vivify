"""``auto-heal goals`` — show / add / decompose."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from auto_heal.config.loader import load_config
from auto_heal.goals.parser import parse_goals


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("goals", help="Inspect / mutate GOALS.md.")
    sp = p.add_subparsers(dest="goals_cmd", required=True)

    show = sp.add_parser("show", help="Show parsed goals.")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=run_show)

    add = sp.add_parser("add", help="Append a new goal section.")
    add.add_argument("--name", required=True)
    add.add_argument("--description", default="")
    add.add_argument("--kpi", action="append", default=[],
                     help="NAME=TARGET[:DIRECTION[:UNIT]] (repeatable)")
    add.set_defaults(func=run_add)

    dec = sp.add_parser("decompose", help="Run goal-to-feature decomposition.")
    dec.add_argument("--goal", help="Limit to a single goal name.")
    dec.add_argument("--dry-run", action="store_true",
                     help="Print proposed FeatureSpecs but do not persist.")
    dec.set_defaults(func=run_decompose)


def _goals_path(args: argparse.Namespace) -> Path:
    cfg = load_config(getattr(args, "config", None))
    return Path(cfg.goals.path)


def run_show(args: argparse.Namespace) -> int:
    path = _goals_path(args)
    if not path.exists():
        print(f"GOALS.md not found at {path}")
        return 1
    try:
        doc = parse_goals(path.read_text(encoding="utf-8"))
    except ValueError as e:
        print(f"parse error: {e}")
        return 2
    if args.json:
        out = {
            "version": doc.version, "owner": doc.owner,
            "review_cadence": doc.review_cadence,
            "goals": [
                {
                    "name": g.name, "description": g.description,
                    "deadline": g.deadline.isoformat() if g.deadline else None,
                    "kpis": [k.__dict__ for k in g.kpis],
                    "notes": g.notes,
                }
                for g in doc.goals
            ],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    print(f"# Parsed {len(doc.goals)} goal(s) from {path}")
    for g in doc.goals:
        print(f"\n## {g.name}")
        if g.description:
            print(f"{g.description}")
        for k in g.kpis:
            print(f"- KPI: {k.name} target={k.target} direction={k.direction} unit={k.unit}")
        if g.deadline:
            print(f"- Deadline: {g.deadline.isoformat()}")
        if g.notes:
            print(f"- Notes: {g.notes}")
    return 0


def run_add(args: argparse.Namespace) -> int:
    path = _goals_path(args)
    lines = [f"\n## Goal: {args.name}"]
    if args.description:
        lines.append(args.description)
    for kpi in args.kpi:
        if "=" not in kpi:
            print(f"skip malformed --kpi: {kpi}")
            continue
        name, rest = kpi.split("=", 1)
        parts = rest.split(":")
        target = parts[0]
        direction = parts[1] if len(parts) > 1 else "up"
        unit = parts[2] if len(parts) > 2 else ""
        line = f"- KPI: {name} target={target} direction={direction}"
        if unit:
            line += f" unit={unit}"
        lines.append(line)
    text = "\n".join(lines) + "\n"
    if not path.exists():
        path.write_text(
            "---\nversion: 1\n---\n\n# Project Goals\n" + text, encoding="utf-8",
        )
    else:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)
    print(f"added goal '{args.name}' to {path}")
    return 0


def run_decompose(args: argparse.Namespace) -> int:
    cfg = load_config(getattr(args, "config", None))
    path = Path(cfg.goals.path)
    if not path.exists():
        print(f"GOALS.md not found at {path}")
        return 1
    doc = parse_goals(path.read_text(encoding="utf-8"))
    targets = [g for g in doc.goals if (not args.goal or g.name == args.goal)]
    if not targets:
        print("no matching goals")
        return 1

    # Lazy build to keep the CLI startup cheap.
    from auto_heal.agents.qodercli_agent import QoderCliAgent, QoderCliConfig as AC
    from auto_heal.goals.decomposer import AgentGoalDecomposer, GoalDecomposerConfig
    from auto_heal.interfaces.goal_decomposer import RepoState
    from auto_heal.storage.sqlite_provider import SqliteStorageProvider
    from auto_heal.models.feature import FeatureRequest

    qc = cfg.agent.qodercli
    agent = QoderCliAgent(AC(
        binary_path=qc.binary_path, model=qc.model,
        extra_args=tuple(qc.extra_args),
        max_turns_default=qc.max_turns_decompose,
        timeout_seconds_default=qc.timeout_decompose_seconds,
    ))
    decomposer = AgentGoalDecomposer(
        agent=agent, repo_root=Path.cwd(),
        config=GoalDecomposerConfig(
            max_features_per_decompose=cfg.goals.max_features_per_decompose,
        ),
    )

    storage = SqliteStorageProvider(cfg.storage.sqlite.path)
    storage.initialize()
    open_features = []
    for status in ("pending", "approved"):
        open_features.extend(storage.list_features(status=status, limit=200))
    snapshots = []  # decomposer falls back to "no snapshots" gracefully

    state = RepoState(repo_root=str(Path.cwd()), default_branch=cfg.pr.base_branch)
    total_created = 0
    for goal in targets:
        specs = decomposer.decompose(goal, state, open_features, snapshots)
        print(f"\n## Goal: {goal.name} → {len(specs)} feature(s)")
        for s in specs:
            print(f"- [{s.type}/{s.priority or '?'}] {s.title}\n    {s.description[:200]}")
            if not args.dry_run:
                storage.create_feature(FeatureRequest(
                    title=s.title, description=s.description, type=s.type,
                    parent_goal=goal.name, priority=s.priority,
                ))
                total_created += 1
    if not args.dry_run:
        print(f"\ncreated {total_created} feature request(s)")
    return 0


__all__ = ["register", "run_show", "run_add", "run_decompose"]
