# auto-heal

Self-growing intelligent extension you can mount to **any** GitHub project. It autonomously
monitors system health, lets requirements/bugs be self-submitted and self-resolved, and
iterates new features toward the goals you declared in `GOALS.md` — all driven by Qoder CLI
behind the scenes.

> Distilled and generalized from the `auto_heal` module of `pinsonchen/channels-monitor`,
> stripped of all business-specific code, redesigned to be project-agnostic.

## Why

Every long-lived project drifts: tests rot, dependencies age, lint debt grows, goals get
lost. `auto-heal` runs as a daemon next to your repo and:

1. **Detects** drift via 12+ pluggable probes (CI failures, vulns, low coverage, lint debt,
   issue backlog, doc staleness, dead code, leaked secrets, …).
2. **Fixes** what it safely can with built-in fixers (dependency bumps, lint/format
   autofix, flaky-test triage, stale-branch prune, …).
3. **Escalates** what it can't fix into requirements (kept in SQLite + mirrored to GitHub
   Issues).
4. **Iterates** by reading your `GOALS.md`, decomposing each goal into actionable feature
   requests, and developing them via Qoder CLI in isolated `git worktree`s.
5. **Lands** every change as a Pull Request — no direct pushes to `main`.
6. **Self-grows**: the AI is allowed to refine its own probes/fixers/prompts (under a
   path whitelist), so the system gets sharper the longer it runs.

## Quick start (5 minutes)

```bash
pip install auto-heal-cli            # or: pip install -e . from a clone
cd /path/to/your/repo
auto-heal init                       # interactive scaffold
auto-heal doctor                     # validates env (git, gh, qodercli, GH_TOKEN)
auto-heal run --once --dry-run       # see issues without opening PRs
auto-heal run                        # daemon
```

## Requirements

- Python ≥ 3.10
- `git`, `gh` (GitHub CLI), `qodercli` available on `PATH`
- `GH_TOKEN` env var (or `gh auth login` once)
- A GitHub repo with branch protection (recommended) if you enable `auto_merge`

## What gets created in your repo

```
.auto-heal.yml                # config (committed; secrets via env)
GOALS.md                      # your project goals & KPIs (committed)
.auto-heal/
├── state.db                  # SQLite — feature pool, logs, knowledge (gitignored)
├── logs/                     # daily logs (gitignored)
├── worktrees/                # AI-developed branches (gitignored)
├── probes/                   # add your own .py / .yml probes
├── fixers/                   # add your own .py fixers
└── pr_template.md            # PR body template
```

## Concepts

- **Probe** — declarative (YAML) or programmatic (Python) detector. Emits `Issue`s.
- **Fixer** — fast-path remediation that doesn't need an LLM (e.g. `ruff --fix`). Always
  opens a PR.
- **Issue** → unfixed N rounds → **FeatureRequest** → evaluated → developed in worktree →
  PR → verified → knowledge.
- **Goal** (in `GOALS.md`) → KPIs → KPI-monitor probe → degrade alert → goal-decomposer →
  new feature requests.
- **Self-growth**: AI may edit `auto_heal/probes/builtin/`, `auto_heal/fixers/builtin/`,
  prompt templates, but never the kernel without two human approvals.

## CLI

```
auto-heal init [--non-interactive] [--repo PATH] [--force]
auto-heal run  [--once] [--dry-run] [--category CAT] [--interval N]
auto-heal doctor
auto-heal goals    show | add ... | decompose [--goal NAME] [--dry-run]
auto-heal probes   list | test <id> | enable/disable <id>
auto-heal fixers   list | test <id> --issue-file FILE
auto-heal features list [--status S] | show <id> | retry <id>
auto-heal logs     tail [-n N] [--follow]
```

## Configuration

See `.auto-heal.example.yml` and `docs/`. All keys can be overridden by environment
variables: `AUTO_HEAL__<DOTTED_PATH>` (double underscore between path segments).

## Status

Alpha. The kernel + SQLite storage + Qoder CLI agent + PR mode + 6 builtin probes +
2 builtin fixers + goal decomposer are functional and tested. Roadmap: GitHub
Actions runner, Docker image, multi-agent (Claude Code/Codex) support, Web console.

## License

MIT — see `LICENSE`.
