"""``vivify init`` — scaffold a new repo for vivify use."""
from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path

from vivify.config.defaults import DEFAULT_GITIGNORE_ENTRIES


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help="Scaffold a repo for vivify use.")
    p.add_argument("--repo", default=".", help="Target repo path (defaults to cwd).")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing .vivify.yml / GOALS.md.")
    p.add_argument("--non-interactive", action="store_true",
                   help="Use defaults without prompting.")
    p.set_defaults(func=run)


def _copy_template(name: str, dest: Path, *, force: bool) -> bool:
    src = files("vivify.templates").joinpath(name)
    if dest.exists() and not force:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def _patch_gitignore(repo: Path) -> None:
    gi = repo / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    add = [e for e in DEFAULT_GITIGNORE_ENTRIES if e not in existing]
    if not add:
        return
    block = "\n# vivify\n" + "\n".join(add) + "\n"
    with gi.open("a", encoding="utf-8") as fh:
        fh.write(block)


def _write_user_dir_readmes(repo: Path) -> None:
    probes_dir = repo / ".vivify" / "probes"
    fixers_dir = repo / ".vivify" / "fixers"
    probes_dir.mkdir(parents=True, exist_ok=True)
    fixers_dir.mkdir(parents=True, exist_ok=True)
    (probes_dir / "README.md").write_text(
        "# User probes\n\nDrop `.yml` or `.py` probe definitions here. "
        "See https://github.com/pinsonchen/vivify/tree/main/docs/probes.md\n",
        encoding="utf-8",
    )
    (fixers_dir / "README.md").write_text(
        "# User fixers\n\nDrop Python modules with a `FIXER` (or `FIXERS`) export here.\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    print(f"vivify init → {repo}")

    cfg_dest = repo / ".vivify.yml"
    goals_dest = repo / "GOALS.md"
    pr_dest = repo / ".vivify" / "pr_template.md"

    cfg_written = _copy_template("vivify.yml.tmpl", cfg_dest, force=args.force)
    goals_written = _copy_template("GOALS.md.tmpl", goals_dest, force=args.force)
    pr_written = _copy_template("pr_template.md.tmpl", pr_dest, force=args.force)

    _write_user_dir_readmes(repo)
    _patch_gitignore(repo)

    print(f"  .vivify.yml      {'created' if cfg_written else 'already exists (use --force to overwrite)'}")
    print(f"  GOALS.md            {'created' if goals_written else 'already exists'}")
    print(f"  .vivify/pr_template.md {'created' if pr_written else 'already exists'}")
    print("  .vivify/probes/  ready (drop your YAML/Python probes here)")
    print("  .vivify/fixers/  ready (drop your Python fixers here)")
    print()
    print("Next steps:")
    print("  vivify doctor")
    print("  vivify run --once --dry-run")
    print("  vivify run")
    return 0


__all__ = ["register", "run"]
